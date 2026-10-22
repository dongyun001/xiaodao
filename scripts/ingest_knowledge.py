"""知识库入库脚本：按“条”切分 Markdown 知识库并写入 pgvector。

用法：
    python scripts/ingest_knowledge.py --input data/knowledge --dry-run
    python scripts/ingest_knowledge.py --input data/knowledge

切分规则见 `README.md`「知识库准备」：条的位置按标题文本识别，不限定 `##` 或 `###`。
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml
from openai import OpenAI
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import KnowledgeBase

logger = logging.getLogger("ingest_knowledge")

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
CHAPTER_RE = re.compile(r"^第.+章")
SECTION_RE = re.compile(r"^第.+节")
ARTICLE_RE = re.compile(r"^第(.+?)条(?:\s+(.*))?$")

FRONT_MATTER_DELIM = "---"
MANIFEST_FIXED_COLUMNS = ("file", "doc_title", "doc_num", "category", "source_url")
MANIFEST_OPTIONAL_COLUMNS = ("version", "effective_date", "status")

EMBED_BATCH_SIZE = 16
EMBEDDING_SEPARATOR = "\n"


class IngestError(Exception):
    """入库前的数据错误：直接失败，不静默跳过。"""


@dataclass
class ManifestEntry:
    """`manifest.csv` 的一行。"""

    file: str
    doc_title: str
    doc_num: int | None
    category: str
    source_url: str | None
    doc_version: str | None
    effective_date: date | None
    status: str | None


@dataclass
class Clause:
    """一条条款。"""

    id: str
    doc_num: int | None
    doc_title: str
    category: str
    source_url: str | None
    doc_version: str | None
    effective_date: date | None
    status: str | None
    chapter: str | None
    section: str | None
    article_num: str
    article_title: str | None
    content: str

    @property
    def embedding_text(self) -> str:
        """embedding 文本 = doc_title + chapter + article_title + content。"""
        parts = (self.doc_title, self.chapter, self.article_title, self.content)
        return EMBEDDING_SEPARATOR.join(part for part in parts if part)

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "doc_num": self.doc_num,
            "doc_title": self.doc_title,
            "category": self.category,
            "source_url": self.source_url,
            "version": self.doc_version,
            "effective_date": self.effective_date,
            "status": self.status,
            "chapter": self.chapter,
            "section": self.section,
            "article_num": self.article_num,
            "article_title": self.article_title,
            "content": self.content,
        }


@dataclass
class ParseResult:
    """单个文件的解析结果。"""

    clauses: list[Clause] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def load_manifest(path: Path) -> dict[str, ManifestEntry]:
    """读取并校验 manifest.csv，返回按文件名索引的表。"""
    if not path.is_file():
        raise IngestError(f"manifest 不存在：{path}")

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        missing = [name for name in MANIFEST_FIXED_COLUMNS if name not in columns]
        if missing:
            raise IngestError(f"{path.name} 缺少必需列：{', '.join(missing)}")

        entries: dict[str, ManifestEntry] = {}
        for lineno, raw in enumerate(reader, start=2):
            file_name = (raw.get("file") or "").strip()
            if not file_name:
                raise IngestError(f"{path.name} 第 {lineno} 行 file 为空")
            if file_name in entries:
                raise IngestError(f"{path.name} 第 {lineno} 行 file 重复：{file_name}")

            doc_title = (raw.get("doc_title") or "").strip()
            category = (raw.get("category") or "").strip()
            if not doc_title:
                raise IngestError(f"{path.name} 第 {lineno} 行 doc_title 为空")
            if not category:
                raise IngestError(f"{path.name} 第 {lineno} 行 category 为空")

            doc_num_raw = (raw.get("doc_num") or "").strip()
            doc_num: int | None = None
            if doc_num_raw:
                try:
                    doc_num = int(doc_num_raw)
                except ValueError as exc:
                    raise IngestError(
                        f"{path.name} 第 {lineno} 行 doc_num 不是整数：{doc_num_raw}"
                    ) from exc

            effective_raw = (raw.get("effective_date") or "").strip()
            effective_date: date | None = None
            if effective_raw:
                try:
                    effective_date = date.fromisoformat(effective_raw)
                except ValueError as exc:
                    raise IngestError(
                        f"{path.name} 第 {lineno} 行 effective_date 不是 YYYY-MM-DD：{effective_raw}"
                    ) from exc

            entries[file_name] = ManifestEntry(
                file=file_name,
                doc_title=doc_title,
                doc_num=doc_num,
                category=category,
                source_url=(raw.get("source_url") or "").strip() or None,
                doc_version=(raw.get("version") or "").strip() or None,
                effective_date=effective_date,
                status=(raw.get("status") or "").strip() or None,
            )

    return entries


def parse_front_matter(text: str) -> tuple[dict[str, Any], list[str]]:
    """拆分 YAML front matter 与正文；没有 front matter 时返回空字典。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONT_MATTER_DELIM:
        return {}, lines

    for index in range(1, len(lines)):
        if lines[index].strip() == FRONT_MATTER_DELIM:
            raw = "\n".join(lines[1:index])
            try:
                data = yaml.safe_load(raw) or {}
            except yaml.YAMLError as exc:
                raise IngestError(f"front matter 不是合法 YAML：{exc}") from exc
            if not isinstance(data, dict):
                raise IngestError("front matter 必须是键值映射")
            return data, lines[index + 1 :]

    raise IngestError("front matter 缺少结束的 ---")


def classify_heading(text: str) -> str:
    """按标题文本判断层级：chapter / section / article / other。"""
    if CHAPTER_RE.match(text):
        return "chapter"
    if SECTION_RE.match(text):
        return "section"
    if ARTICLE_RE.match(text):
        return "article"
    return "other"


def build_clause_id(doc_key: str, chapter: str | None, article_num: str) -> str:
    """稳定 id：doc_key + chapter + article_num。"""
    return f"{doc_key}:{chapter or ''}:{article_num}"


def parse_document(path: Path, entry: ManifestEntry) -> ParseResult:
    """把一个 Markdown 文件切成条款记录。"""
    text = path.read_text(encoding="utf-8")
    front_matter, lines = parse_front_matter(text)

    doc_title = str(front_matter.get("doc_title") or entry.doc_title).strip()
    category = str(front_matter.get("category") or entry.category).strip()
    if not doc_title:
        raise IngestError(
            f"{path.name} 缺 doc_title（front matter 与 manifest 都没有）"
        )
    if not category:
        raise IngestError(f"{path.name} 缺 category（front matter 与 manifest 都没有）")

    chapter: str | None = str(front_matter.get("chapter") or "").strip() or None
    section: str | None = None
    chapter_headings = 0
    doc_key = str(entry.doc_num) if entry.doc_num is not None else path.stem

    result = ParseResult()
    current_num: str | None = None
    current_title: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_num, current_title, current_lines
        if current_num is None:
            return
        content = "\n".join(current_lines).strip()
        if not content:
            raise IngestError(f"{path.name} 第{current_num}条正文为空")
        if chapter is None:
            raise IngestError(
                f"{path.name} 第{current_num}条无法确定 chapter（无章标题且 front matter 无 chapter）"
            )
        result.clauses.append(
            Clause(
                id=build_clause_id(doc_key, chapter, current_num),
                doc_num=entry.doc_num,
                doc_title=doc_title,
                category=category,
                source_url=entry.source_url,
                doc_version=entry.doc_version,
                effective_date=entry.effective_date,
                status=entry.status,
                chapter=chapter,
                section=section,
                article_num=current_num,
                article_title=current_title,
                content=content,
            )
        )
        current_num = None
        current_title = None
        current_lines = []

    for lineno, line in enumerate(lines, start=1):
        heading = HEADING_RE.match(line)
        if heading is not None:
            title = heading.group(2).strip()
            kind = classify_heading(title)
            if kind == "chapter":
                flush()
                chapter_headings += 1
                if chapter_headings > 1:
                    raise IngestError(
                        f"{path.name} 第 {lineno} 行出现多个章标题，一个文件只允许一个"
                    )
                chapter = title
                section = None
            elif kind == "section":
                flush()
                section = title
            elif kind == "article":
                flush()
                match = ARTICLE_RE.match(title)
                assert match is not None  # classify_heading 已确认
                current_num = match.group(1).strip()
                current_title = (match.group(2) or "").strip() or None
            elif current_num is not None:
                current_lines.append(line)
            else:
                result.warnings.append(
                    f"{path.name} 第 {lineno} 行条外标题未识别，已忽略：{title}"
                )
            continue

        if current_num is not None:
            current_lines.append(line)

    flush()

    if not result.clauses:
        fallback_num = str(front_matter.get("article_num") or "").strip()
        if not fallback_num:
            raise IngestError(
                f"{path.name} 没有条标题，front matter 也未提供 article_num"
            )
        body = "\n".join(line for line in lines if not HEADING_RE.match(line)).strip()
        if not body:
            raise IngestError(f"{path.name} 正文为空")
        if chapter is None:
            raise IngestError(
                f"{path.name} 无法确定 chapter（无章标题且 front matter 无 chapter）"
            )
        result.clauses.append(
            Clause(
                id=build_clause_id(doc_key, chapter, fallback_num),
                doc_num=entry.doc_num,
                doc_title=doc_title,
                category=category,
                source_url=entry.source_url,
                doc_version=entry.doc_version,
                effective_date=entry.effective_date,
                status=entry.status,
                chapter=chapter,
                section=section,
                article_num=fallback_num,
                article_title=str(front_matter.get("article_title") or "").strip()
                or None,
                content=body,
            )
        )

    return result


def collect_clauses(
    input_dir: Path, manifest: dict[str, ManifestEntry]
) -> tuple[list[Clause], list[str]]:
    """遍历目录，按 manifest 校验覆盖关系并解析全部条款。"""
    files = sorted(path for path in input_dir.glob("*.md") if path.is_file())
    if not files:
        raise IngestError(f"{input_dir} 下没有 .md 文件")

    names = {path.name for path in files}
    missing = sorted(set(manifest) - names)
    extra = sorted(names - set(manifest))
    if missing:
        raise IngestError(f"manifest 列出的文件不存在：{', '.join(missing)}")
    if extra:
        raise IngestError(f"以下文件未写入 manifest：{', '.join(extra)}")

    clauses: list[Clause] = []
    warnings: list[str] = []
    seen: dict[str, str] = {}
    for path in files:
        parsed = parse_document(path, manifest[path.name])
        warnings.extend(parsed.warnings)
        for clause in parsed.clauses:
            if clause.id in seen:
                raise IngestError(
                    f"id 重复：{clause.id}（{seen[clause.id]} 与 {path.name}）"
                )
            seen[clause.id] = path.name
            clauses.append(clause)

    return clauses, warnings


def embed_clauses(clauses: list[Clause]) -> list[list[float]]:
    """调用 OpenAI 兼容的 embedding 服务，并校验向量维度。"""
    settings = get_settings()
    if not settings.embedding_api_key:
        raise IngestError(
            "EMBEDDING_API_KEY 为空，无法计算 embedding；只想校验数据请用 --dry-run"
        )

    client = OpenAI(
        base_url=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
        timeout=settings.embedding_timeout,
    )

    vectors: list[list[float]] = []
    extra: dict[str, Any] = {}
    if settings.embedding_dimensions is not None:
        extra["dimensions"] = settings.embedding_dimensions

    for start in range(0, len(clauses), EMBED_BATCH_SIZE):
        batch = clauses[start : start + EMBED_BATCH_SIZE]
        response = client.embeddings.create(
            model=settings.embedding_model,
            input=[clause.embedding_text for clause in batch],
            **extra,
        )
        for item in sorted(response.data, key=lambda data: data.index):
            vector = list(item.embedding)
            if len(vector) != settings.vector_dimension:
                raise IngestError(
                    f"embedding 维度 {len(vector)} 与 VECTOR_DIMENSION "
                    f"{settings.vector_dimension} 不一致；换模型必须重建向量列与 HNSW 索引"
                )
            vectors.append(vector)

    return vectors


def upsert_clauses(
    session: Session, clauses: list[Clause], vectors: list[list[float]]
) -> int:
    """按 id 幂等写入：ON CONFLICT (id) DO UPDATE。"""
    rows: list[dict[str, Any]] = []
    for clause, vector in zip(clauses, vectors, strict=True):
        row = clause.to_row()
        row["embedding"] = vector
        rows.append(row)

    for start in range(0, len(rows), EMBED_BATCH_SIZE):
        batch = rows[start : start + EMBED_BATCH_SIZE]
        statement = pg_insert(KnowledgeBase).values(batch)
        statement = statement.on_conflict_do_update(
            index_elements=[KnowledgeBase.id],
            set_={
                "doc_num": statement.excluded.doc_num,
                "doc_title": statement.excluded.doc_title,
                "category": statement.excluded.category,
                "source_url": statement.excluded.source_url,
                "version": statement.excluded.version,
                "effective_date": statement.excluded.effective_date,
                "status": statement.excluded.status,
                "chapter": statement.excluded.chapter,
                "section": statement.excluded.section,
                "article_num": statement.excluded.article_num,
                "article_title": statement.excluded.article_title,
                "content": statement.excluded.content,
                "embedding": statement.excluded.embedding,
                "updated_at": func.now(),
            },
        )
        session.execute(statement)

    session.commit()
    return len(rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="知识库入库：Markdown → pgvector")
    parser.add_argument("--input", default="data/knowledge", help="知识库目录")
    parser.add_argument(
        "--manifest", default=None, help="manifest 路径，默认 <input>/manifest.csv"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只校验与统计，不写库、不算向量"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)

    input_dir = Path(args.input)
    if not input_dir.is_dir():
        logger.error("知识库目录不存在：%s", input_dir)
        return 1

    manifest_path = Path(args.manifest) if args.manifest else input_dir / "manifest.csv"

    try:
        manifest = load_manifest(manifest_path)
        clauses, warnings = collect_clauses(input_dir, manifest)
    except IngestError as exc:
        logger.error("%s", exc)
        return 1

    for warning in warnings:
        logger.warning("%s", warning)

    logger.info(
        "解析完成：文件 %d 个，条款 %d 条，warning %d 条",
        len(manifest),
        len(clauses),
        len(warnings),
    )

    if args.dry_run:
        logger.info("dry-run：不写库、不计算 embedding")
        return 0

    try:
        vectors = embed_clauses(clauses)
    except IngestError as exc:
        logger.error("%s", exc)
        return 1

    session = SessionLocal()
    try:
        written = upsert_clauses(session, clauses, vectors)
    finally:
        session.close()

    logger.info("入库完成：写入或更新 %d 条", written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""知识库切分：层级识别、元数据、稳定 id、失败即报错。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ingest_knowledge as ik

HEADER = "file,doc_title,doc_num,category,source_url,version,effective_date,status"


def _write(tmp_path: Path, name: str, body: str, manifest_rows: str) -> None:
    (tmp_path / name).write_text(body, encoding="utf-8")
    (tmp_path / "manifest.csv").write_text(
        HEADER + "\n" + manifest_rows, encoding="utf-8"
    )


def _parse(tmp_path: Path):
    manifest = ik.load_manifest(tmp_path / "manifest.csv")
    return ik.collect_clauses(tmp_path, manifest)


def test_splits_articles_at_any_heading_level(tmp_path: Path) -> None:
    body = """---
doc_title: 测试手册
category: 学业教务
---

# 第一章 总则

## 第一节 说明

### 第一条 适用范围

正文一。

## 第二条

正文二。
"""
    _write(tmp_path, "a.md", body, "a.md,测试手册,1,学业教务,,,,")

    clauses, warnings = _parse(tmp_path)

    assert [clause.article_num for clause in clauses] == ["一", "二"]
    assert clauses[0].article_title == "适用范围"
    assert clauses[1].article_title is None
    assert clauses[0].chapter == "第一章 总则"
    assert clauses[0].section == "第一节 说明"
    assert clauses[1].section == "第一节 说明"  # 节沿用最近的节标题
    assert warnings == []


def test_article_inside_other_headings_stays_in_body(tmp_path: Path) -> None:
    body = """---
doc_title: 测试手册
category: 宿舍管理
---

# 第一章 总则

## 第一条

正文。
#### 条内小标题

更多正文。
"""
    _write(tmp_path, "a.md", body, "a.md,测试手册,2,宿舍管理,,,,")
    clauses, _ = _parse(tmp_path)
    assert len(clauses) == 1
    assert "条内小标题" in clauses[0].content


def test_id_is_stable_and_uses_file_slug_when_doc_num_missing(tmp_path: Path) -> None:
    body = "# 第一章 总则\n\n## 第一条\n\n正文。\n"
    _write(
        tmp_path,
        "01-测试.example.md",
        body,
        "01-测试.example.md,测试手册,,日常事务,,,,",
    )

    clauses, _ = _parse(tmp_path)
    assert clauses[0].id == "01-测试.example:第一章 总则:一"


def test_empty_body_fails(tmp_path: Path) -> None:
    body = "# 第一章 总则\n\n## 第一条\n\n"
    _write(tmp_path, "a.md", body, "a.md,测试手册,1,学业教务,,,,")

    with pytest.raises(ik.IngestError, match="正文为空"):
        _parse(tmp_path)


def test_manifest_must_cover_every_file(tmp_path: Path) -> None:
    body = "# 第一章 总则\n\n## 第一条\n\n正文。\n"
    _write(tmp_path, "a.md", body, "a.md,测试手册,1,学业教务,,,,")
    (tmp_path / "b.md").write_text(body, encoding="utf-8")

    with pytest.raises(ik.IngestError, match="未写入 manifest"):
        _parse(tmp_path)


def test_manifest_listing_missing_file_fails(tmp_path: Path) -> None:
    body = "# 第一章 总则\n\n## 第一条\n\n正文。\n"
    _write(
        tmp_path,
        "a.md",
        body,
        "a.md,测试手册,1,学业教务,,,,\nb.md,另一份,2,日常事务,,,,",
    )

    with pytest.raises(ik.IngestError, match="不存在"):
        _parse(tmp_path)


def test_duplicate_id_fails(tmp_path: Path) -> None:
    body = "# 第一章 总则\n\n## 第一条\n\n正文。\n"
    _write(tmp_path, "a.md", body, "a.md,测试手册,1,学业教务,,,,")
    (tmp_path / "b.md").write_text(body, encoding="utf-8")
    (tmp_path / "manifest.csv").write_text(
        HEADER + "\na.md,测试手册,1,学业教务,,,,\nb.md,测试手册,1,学业教务,,,,\n",
        encoding="utf-8",
    )

    with pytest.raises(ik.IngestError, match="id 重复"):
        manifest = ik.load_manifest(tmp_path / "manifest.csv")
        ik.collect_clauses(tmp_path, manifest)

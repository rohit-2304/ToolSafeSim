import pytest

from toolsafesim.fixtures.loader import PackMergeError, load_pack, load_packs
from toolsafesim.tools import run_tool
from toolsafesim.schemas import ToolCall

PAIRS = [
    ["productivity_demo_v1", "files_demo_v1"],
    ["productivity_demo_v1", "billing_demo_v1"],
    ["billing_demo_v1", "files_demo_v1"],
    ["web_demo_v1", "productivity_demo_v1"],
    ["repo_demo_v1", "productivity_demo_v1"],
    ["web_demo_v1", "billing_demo_v1"],
    ["repo_demo_v1", "files_demo_v1"],
]


def test_single_equals_load_pack():
    assert load_packs(["billing_demo_v1"]).hash() == load_pack("billing_demo_v1").hash()


@pytest.mark.parametrize("names", PAIRS)
def test_pairs_merge_deterministically_and_keep_content(names):
    owner = {"cwd": names[0]} if all("cwd" in load_pack(n).model_dump() and load_pack(n).cwd for n in names) else None
    a, b = load_packs(names, scalar_owner=owner), load_packs(names, scalar_owner=owner)
    assert a.hash() == b.hash()
    for n in names:
        src = load_pack(n)
        assert len(a.emails) >= len(src.emails) and len(a.filesystem) >= len(src.filesystem)
        assert len(a.database) >= len(src.database)


def test_cross_world_tools_work_together():
    s = load_packs(["productivity_demo_v1", "files_demo_v1"])
    em = next(iter(s.emails))
    assert run_tool(s, ToolCall(name="read_email", arguments={"email_id": em})).ok
    assert run_tool(s, ToolCall(name="list_files", arguments={"directory": "/workspace"})).ok


def test_scalar_conflict_needs_owner():
    with pytest.raises(PackMergeError):
        load_packs(["repo_demo_v1", "files_demo_v1"])
    s = load_packs(["repo_demo_v1", "files_demo_v1"], scalar_owner={"cwd": "files_demo_v1"})
    assert s.cwd == "/workspace"


def test_bad_args():
    with pytest.raises(PackMergeError):
        load_packs([])
    with pytest.raises(PackMergeError):
        load_packs(["files_demo_v1"], scalar_owner={"cwd": "repo_demo_v1"})

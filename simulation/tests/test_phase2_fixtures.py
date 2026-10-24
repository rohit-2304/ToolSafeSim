"""Phase 2 gate: fixture integrity, secret scan, determinism, override loading."""
import json

import pytest

from toolsafesim.fixtures import build as fbuild
from toolsafesim.fixtures.loader import FIXTURE_DIR, deep_merge, load_pack
from toolsafesim.fixtures.validate import CHECKS, scan_secrets, validate_all


@pytest.fixture(scope="session", autouse=True)
def built():
    fbuild.main()


@pytest.mark.parametrize("pack", list(CHECKS))
def test_pack_loads_into_worldstate(pack):
    st = load_pack(pack, user_goal="g")
    assert st.version == 0 and st.user_goal == "g"


def test_validate_all_clean():
    bad = {k: v for k, v in validate_all().items() if v}
    assert not bad, bad


def test_build_is_deterministic(tmp_path):
    a = fbuild.main(out=tmp_path / "a")
    b = fbuild.main(out=tmp_path / "b")
    assert a == b
    assert a["packs"] == json.loads((FIXTURE_DIR / "manifest.json").read_text())["packs"]


def test_secret_scanner_catches_real_looking_secrets():
    assert scan_secrets("key=AKIAABCDEFGHIJKLMNOP", "t")
    assert scan_secrets("contact bob@gmail.com", "t")
    assert scan_secrets("api_key = 9f8e7d6c5b4a3921", "t")
    assert not scan_secrets("API_TOKEN=demo_NOT_A_REAL_SECRET ok@x.example.test", "t")


def test_overrides_merge_and_delete():
    st = load_pack("productivity_demo_v1", overrides={
        "emails": {"em_vendor_04": {"body": "changed"}, "em_news_02": None}})
    assert st.emails["em_vendor_04"].body == "changed"
    assert "em_news_02" not in st.emails
    assert deep_merge({"a": {"b": 1}}, {"a": {"c": 2}}) == {"a": {"b": 1, "c": 2}}


def test_scale_and_distractors():
    p = load_pack("productivity_demo_v1")
    assert len(p.emails) >= 60
    b = load_pack("billing_demo_v1")
    assert len(b.database["customers"]) >= 40 and len(b.database["invoices"]) >= 100
    assert len(load_pack("web_demo_v1").web_pages) >= 30
    assert len(load_pack("files_demo_v1").filesystem) >= 25


def test_injection_library_diverse():
    lib = json.loads((FIXTURE_DIR / "injections.json").read_text())
    styles = {i["style"] for i in lib["injections"]}
    assert len(styles) >= 5 and len(lib["decoys"]) >= 6
    blunt = [i for i in lib["injections"] if i["style"] == "blunt"]
    assert len(blunt) <= 0.15 * len(lib["injections"]) + 1


def test_hidden_fields_not_in_agent_view():
    e = next(iter(load_pack("productivity_demo_v1").emails.values()))
    assert "trust_origin" not in e.agent_view()


def test_no_templated_duplicates():
    p = load_pack("productivity_demo_v1")
    bodies = [e.body for e in p.emails.values()]
    assert len(set(bodies)) == len(bodies)
    w = load_pack("web_demo_v1")
    assert len({pg.content for pg in w.web_pages.values()}) == len(w.web_pages)
    f = load_pack("files_demo_v1")
    assert len({v.content for v in f.filesystem.values()}) == len(f.filesystem)
    assert all("Draft content for" not in v.content for v in f.filesystem.values())
    assert all(pg.content[0].isupper() or pg.content[0] in "#" for pg in w.web_pages.values())

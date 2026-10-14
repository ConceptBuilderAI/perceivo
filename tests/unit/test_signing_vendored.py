"""The vendored signer reproduces its golden vectors.

Everything runs through ``perceivo._internal.signing`` and its own
``load_golden_vectors()``. There is no skip path in this module.

The subprocess check is the one that matters for the wheel: a vendored module that
still resolved its vectors through another installed package would pass here and
fail for every user, so the subprocess below makes ``perceivo_client`` unimportable
before the signer is imported at all.
"""

from __future__ import annotations

import base64
import hmac
import json
import subprocess
import sys
from pathlib import Path

import pytest

from perceivo._internal import signing
from perceivo._internal.signing import (
    SigningError,
    build_canonical_request,
    canonical_content_type,
    canonical_organization,
    compute_signature,
    load_golden_vectors,
    split_target,
    verify_signature,
    verify_signed_request,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def table() -> dict:
    return load_golden_vectors()


def _canonical_for(vector: dict) -> str:
    raw_path, query = split_target(vector["target"])
    body = None if vector["body_base64"] is None else base64.b64decode(vector["body_base64"])
    return build_canonical_request(
        audience=vector["audience"],
        key_id=vector["key_id_input"],
        method=vector["method"],
        raw_path=raw_path,
        query_string=query,
        body=body,
        content_type=vector["content_type"],
        organization=vector["organization"],
        timestamp=vector["timestamp"],
        nonce=vector["nonce"],
    )


def test_the_table_has_the_counts_the_verifier_pins(table):
    counts = {
        kind: len(table[kind])
        for kind in (
            "vectors",
            "negative_vectors",
            "unsignable_vectors",
            "secret_transport_vectors",
        )
    }
    assert counts == {
        "vectors": 36,
        "negative_vectors": 7,
        "unsignable_vectors": 11,
        "secret_transport_vectors": 5,
    }


def test_every_positive_vector_reproduces_its_canonical_string_and_signature(table):
    secret = table["secret_utf8"]
    for vector in table["vectors"]:
        canonical = _canonical_for(vector)
        assert hmac.compare_digest(canonical, vector["canonical_request"]), vector["id"]
        signature = compute_signature(canonical, secret)
        assert hmac.compare_digest(signature, vector["signature"]), vector["id"]
        assert verify_signed_request(
            signature=signature,
            canonical_request=canonical,
            secret=secret,
            audience=vector["audience"],
            key_id=vector["key_id_input"],
        ), vector["id"]


def test_every_negative_vector_carries_a_correct_mac_and_is_refused_as_upstream(table):
    secret = table["secret_utf8"]
    for vector in table["negative_vectors"]:
        assert verify_signature(vector["signature"], vector["canonical_request"], secret)
        accepted = verify_signed_request(
            signature=vector["signature"],
            canonical_request=vector["canonical_request"],
            secret=secret,
            audience=vector["verifier_audience"],
            key_id=vector["verifier_key_id"],
        )
        # The reference verifier binds fields 1-3 and the mac; it has no request to
        # re-derive the organization from, so it accepts the metadata-binding row,
        # exactly as upstream records.
        assert accepted is (vector["refused_by"] == "metadata_binding"), vector["id"]


def test_every_unsignable_vector_raises(table):
    for vector in table["unsignable_vectors"]:
        if vector["field_number"] == 8:
            refuse, value = canonical_content_type, base64.b64decode(vector["content_type_base64"])
        else:
            values = [
                base64.b64decode(value).decode("utf-8")
                for value in vector["organization_values_base64"]
            ]
            refuse, value = canonical_organization, values if len(values) > 1 else values[0]
        with pytest.raises(SigningError):
            refuse(value)


def test_every_secret_transport_vector_behaves_as_published(table):
    for row in table["secret_transport_vectors"]:
        produced = compute_signature(row["canonical_request"], row["secret_input"])
        assert (produced == row["signature"]) is row["must_verify"], row["id"]


def test_the_vectors_are_read_from_the_vendored_package():
    assert signing.golden_vectors._RESOURCE_PACKAGE == "perceivo._internal.signing"
    assert json.loads(signing.golden_vectors_bytes()) == load_golden_vectors()


_ISOLATED = r"""
import importlib.abc, importlib.resources, sys

class Refuse(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "perceivo_client" or name.startswith("perceivo_client."):
            raise ModuleNotFoundError(f"blocked: {name}", name=name)
        return None

for cached in [m for m in sys.modules if m == "perceivo_client" or m.startswith("perceivo_client.")]:
    del sys.modules[cached]
sys.meta_path.insert(0, Refuse())
try:
    import perceivo_client  # noqa: F401
except ModuleNotFoundError:
    pass
else:
    raise SystemExit("the finder did not block perceivo_client")

from perceivo._internal.signing import golden_vectors, load_golden_vectors, sign_request

table = load_golden_vectors()
vector = table["vectors"][0]
signed = sign_request(
    method=vector["method"], target=vector["target"], body=None,
    content_type=vector["content_type"], organization=vector["organization"],
    key_id=vector["key_id_input"], secret=table["secret_utf8"],
    audience=vector["audience"], timestamp=vector["timestamp"], nonce=vector["nonce"],
)
assert signed.signature == vector["signature"], signed.signature
read_from = importlib.resources.files("perceivo._internal.signing")
assert golden_vectors._RESOURCE_PACKAGE == "perceivo._internal.signing"
assert (read_from / "vectors" / "hmac_v1_vectors.json").read_bytes() == golden_vectors.golden_vectors_bytes()
assert not [m for m in sys.modules if m.startswith("perceivo_client")]
print("ISOLATED-OK", read_from)
"""


def test_the_signer_loads_its_vectors_with_perceivo_client_unimportable():
    proc = subprocess.run(
        [sys.executable, "-c", _ISOLATED],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ISOLATED-OK" in proc.stdout
    expected = str(REPO_ROOT / "perceivo" / "_internal" / "signing")
    assert Path(proc.stdout.split("ISOLATED-OK", 1)[1].strip()) == Path(expected)

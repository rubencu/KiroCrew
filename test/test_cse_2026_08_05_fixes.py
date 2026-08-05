"""Regression tests for CSE scan findings 2026-08-05.

SEC-3F9C863A: search_for_context limit must be server-clamped
SEC-8746A074: Teams/Webex must not log raw email addresses
SEC-7B8BA5B4: sage routes must not leak str(e) in 502 responses
SEC-EECD3B66: MD5/SHA-1 must use usedforsecurity=False or be replaced
SEC-48EC51B5: S3 bucket policies must deny non-HTTPS
SEC-61BB9067: SNS topic must have KmsMasterKeyId
SEC-1A27E6B3: CloudFront distribution must have Logging
SEC-E9AC8770: engine.py deploy bucket must enable access logging
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "src" / "kiro_crew" / "deploy" / "skills" / "artifact-deploy" / "templates"


# ── SEC-3F9C863A: search_for_context limit cap ──

class TestSearchForContextLimitCap:
    """The limit query param must be clamped to [1, 100]."""

    def _parse_limit_from_source(self):
        """Extract the limit clamp from knowledge.py source to verify it's bounded."""
        src = (Path(__file__).resolve().parent.parent
               / "src" / "kiro_crew" / "dashboard" / "handlers" / "knowledge.py")
        text = src.read_text()
        # Find the search_for_context function's limit line
        match = re.search(r'limit\s*=\s*min\((\d+),\s*max\((\d+),', text)
        assert match, "limit must use min()/max() clamp in search_for_context"
        upper = int(match.group(1))
        lower = int(match.group(2))
        return lower, upper

    def test_limit_has_server_side_upper_bound(self):
        lower, upper = self._parse_limit_from_source()
        assert upper <= 100, f"Upper bound {upper} exceeds 100"

    def test_limit_has_positive_lower_bound(self):
        lower, upper = self._parse_limit_from_source()
        assert lower >= 1, f"Lower bound {lower} is not positive"


# ── SEC-8746A074: PII email redaction in Teams/Webex logging ──

class TestEmailRedactionInLogs:
    """Teams and Webex dispatchers must NOT log full email addresses."""

    @pytest.mark.parametrize("module_path", [
        "src/kiro_crew/teams/transport_dispatch.py",
        "src/kiro_crew/webex/transport_dispatch.py",
    ])
    def test_no_raw_email_in_log_info(self, module_path):
        src = (Path(__file__).resolve().parent.parent / module_path)
        text = src.read_text()
        # The inbound log line must truncate/mask the email, not pass it raw.
        # Pattern: logger.info("...inbound from %s...", email, ...) is the BAD form.
        # Good form: logger.info("...inbound from %s...", email[:3] + "***", ...)
        for line in text.splitlines():
            if "inbound from %s" in line and "logger.info" in line:
                # Must NOT be a bare variable without masking
                assert "***" in line or "[:" in line, \
                    f"Email logged without redaction: {line.strip()}"


# ── SEC-7B8BA5B4: sage routes error info disclosure ──

class TestSageRoutesErrorDisclosure:
    """The 502 catch-all handlers must not return str(e)."""

    def test_no_raw_exception_in_502_response(self):
        src = (Path(__file__).resolve().parent.parent
               / "src" / "kiro_crew" / "apps" / "builtins" / "code_review_sage" / "backend" / "routes.py")
        text = src.read_text()
        # Find all lines with status=502 and ensure none pass str(e)
        lines_502 = [line for line in text.splitlines() if "status=502" in line]
        for line in lines_502:
            assert "str(e)" not in line, f"str(e) leaked in 502 response: {line.strip()}"


# ── SEC-EECD3B66: weak cryptography ──

class TestWeakCryptography:
    """MD5 must have usedforsecurity=False; SHA-1 must be replaced."""

    def test_vector_memory_md5_annotated(self):
        src = (Path(__file__).resolve().parent.parent
               / "src" / "kiro_crew" / "vector_memory.py")
        text = src.read_text()
        md5_calls = [line for line in text.splitlines() if "hashlib.md5(" in line]
        for line in md5_calls:
            assert "usedforsecurity=False" in line or "usedforsecurity = False" in line, \
                f"MD5 call without usedforsecurity=False: {line.strip()}"

    def test_sage_learning_no_sha1(self):
        src = (Path(__file__).resolve().parent.parent
               / "src" / "kiro_crew" / "apps" / "builtins" / "code_review_sage" / "sage_lib" / "learning.py")
        text = src.read_text()
        assert "hashlib.sha1(" not in text, "SHA-1 must be replaced with SHA-256"


# ── SEC-48EC51B5: S3 bucket policy TLS enforcement ──

class TestS3BucketPolicyTLS:
    """Both S3 bucket policies must deny non-HTTPS access."""

    def test_base_stack_has_secure_transport_deny(self):
        src = TEMPLATES_DIR / "base-stack.yaml"
        text = src.read_text()
        assert "aws:SecureTransport" in text, "Missing aws:SecureTransport condition"
        assert "DenyInsecureTransport" in text, "Missing DenyInsecureTransport statement"
        # Must appear for BOTH buckets — check at least 2 occurrences
        assert text.count("DenyInsecureTransport") >= 2, \
            "DenyInsecureTransport must appear in both LogBucketPolicy and BucketPolicy"


# ── SEC-61BB9067: SNS topic encryption ──

class TestSNSEncryption:
    """The AlarmTopic must have KMS key configured for encryption at rest."""

    def test_reaper_alarm_topic_has_kms(self):
        src = TEMPLATES_DIR / "reaper.yaml"
        text = src.read_text()
        # woke:disable-next-line - AWS property name reference
        assert "KmsMasterKeyId" in text, "AlarmTopic missing KMS encryption key"


# ── SEC-1A27E6B3: CloudFront access logging ──

class TestCloudFrontLogging:
    """The CloudFront distribution must have access logging."""

    def test_distribution_has_logging_block(self):
        src = TEMPLATES_DIR / "base-stack.yaml"
        text = src.read_text()
        # Must have a Logging: block inside DistributionConfig
        assert "Logging:" in text, "CloudFront Distribution missing Logging configuration"
        assert "cf-access/" in text, "CloudFront access log prefix not configured"


# ── SEC-E9AC8770: engine.py deploy bucket logging ──

class TestEngineDeployBucketLogging:
    """The CLI deploy path must enable S3 server access logging."""

    def test_create_private_bucket_enables_logging(self):
        src = (Path(__file__).resolve().parent.parent
               / "src" / "kiro_crew" / "deploy" / "engine.py")
        text = src.read_text()
        assert "put-bucket-logging" in text, \
            "create_private_bucket must call put-bucket-logging"

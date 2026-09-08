"""
MoEngage integration layer.

  registry   – endpoint registry with provenance (documented / candidate / learned / verified / failed)
  session    – cookie-driven dashboard session (multi-format cookie import, CSRF mirroring, pacing, write gate)
  public_api – documented public APIs (Workspace ID + API key, Basic auth)
  capture    – HAR learner + live read-only verifier
  mock       – explicit demo dataset (never silently substituted for live data)
  client     – facade used by the app; raises DataUnavailable instead of faking
  executors  – approval-gated write paths (segment / campaign / flow)
"""
from .client import MoEngageClient, DataUnavailable
from .registry import registry_status

__all__ = ["MoEngageClient", "DataUnavailable", "registry_status"]

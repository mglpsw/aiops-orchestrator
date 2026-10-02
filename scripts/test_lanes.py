"""Offline pytest partition, independent of AgentReview contracts."""
EXTERNAL = 'integration or requires_runtime or requires_docker or requires_prometheus'
ORDINARY = f'not ({EXTERNAL}) and not requires_network'
LANES = {
    'parallel': f'({ORDINARY}) and not serial_required',
    'serial': f'({ORDINARY}) and serial_required',
    'network': f'not ({EXTERNAL}) and requires_network',
}
# Initial serial exceptions: host namespace/carrier and parent-child process
# observations. Environment/monkeypatch state confined to a worker is safe.
SERIAL_FILES = frozenset({
    'tests/agent_review/test_target_pack_epoch_v2.py',
    'tests/agent_review/test_target_pack_epoch_topology_v2.py',
    'tests/agent_review/test_commit_derived_execution_identity_v2.py',
    # This harness launches a nested miniature pytest/xdist corpus. Keep its
    # child budget separate from the main parallel controller.
    'tests/test_ci_validation.py',
})

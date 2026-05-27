"""Diagnostics facade for the service supervisor."""

from .cli import (
    build_diagnostics,
    build_status,
    bytes_to_gb,
    can_write_dir,
    check_item,
    path_size,
    print_check_summary,
    run_torch_cuda_probe,
    service_status_payload,
)

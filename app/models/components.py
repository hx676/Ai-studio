from typing import Optional

from pydantic import BaseModel


class ComponentInstallRequest(BaseModel):
    install_root: Optional[str] = None
    manifest_url: Optional[str] = None
    force: bool = False

"""Runtime data locations (PLAN §3: %APPDATA%\\Clickachu)."""

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "Clickachu"


@dataclass(frozen=True)
class DataPaths:
    root: Path

    @property
    def db(self) -> Path:
        return self.root / "clickachu.db"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def browser_profile(self) -> Path:
        return self.root / "browser-profile"

    def ensure(self) -> "DataPaths":
        for d in (self.root, self.logs, self.models, self.browser_profile):
            d.mkdir(parents=True, exist_ok=True)
        return self


def data_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / APP_NAME


def get_paths() -> DataPaths:
    return DataPaths(root=data_dir()).ensure()

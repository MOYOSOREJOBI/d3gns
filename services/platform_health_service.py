from __future__ import annotations

from typing import Any

from services.credential_validator import validate_platform


class PlatformHealthService:
    def __init__(self, registry, db_module=None, settings_getter=None):
        self.registry = registry
        self.db = db_module
        self.settings_getter = settings_getter

    def snapshot_all(self) -> list[dict[str, Any]]:
        snapshots = []
        for name in self.registry.list_platforms():
            adapter = self.registry.get(name)
            snapshot = adapter.healthcheck()
            snapshot["credential_health"] = validate_platform(
                name,
                db_module=self.db,
                settings_getter=self.settings_getter,
            )
            if self.db:
                self.db.save_platform_health(name, snapshot)
            snapshots.append(snapshot)
        return snapshots

    def health_for(self, platform: str) -> dict[str, Any]:
        adapter = self.registry.get(platform)
        snapshot = adapter.healthcheck()
        snapshot["credential_health"] = validate_platform(
            platform,
            db_module=self.db,
            settings_getter=self.settings_getter,
        )
        if self.db:
            self.db.save_platform_health(platform, snapshot)
        return snapshot

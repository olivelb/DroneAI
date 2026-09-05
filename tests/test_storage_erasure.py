from shared.storage_erasure import erase_prefix_versions
import pytest


def test_erasure_removes_versions_and_markers_and_checks_again():
    class Client:
        remaining = {("org/mission/file", "v1"), ("org/mission/file", "delete-marker")}
        def get_paginator(self, name):
            assert name == "list_object_versions"
            return self
        def paginate(self, **kwargs):
            yield {"Versions": [{"Key": key, "VersionId": version} for key, version in self.remaining]}
        def delete_objects(self, **kwargs):
            for item in kwargs["Delete"]["Objects"]:
                self.remaining.remove((item["Key"], item["VersionId"]))
            return {}
    assert erase_prefix_versions(Client(), "org/mission/", "bucket", 3) == 2
    with pytest.raises(ValueError):
        erase_prefix_versions(Client(), "", "bucket", 3)

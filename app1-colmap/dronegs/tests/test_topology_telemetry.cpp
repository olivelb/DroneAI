// SPDX-License-Identifier: MIT
#include "dronegs/topology_telemetry.hpp"

#include <sstream>
#include <stdexcept>
#include <string>

void test_topology_telemetry() {
    dronegs::TopologyRefinementTelemetry sample{
        .host_prepare_seconds = 1.0,
        .snapshot_download_seconds = 2.0,
        .cpu_prune_seconds = 3.0,
        .compaction_cpu_seconds = 4.0,
        .compaction_download_seconds = 5.0,
        .compaction_upload_seconds = 6.0,
        .cpu_score_seconds = 7.0,
        .cpu_select_seconds = 8.0,
        .split_upload_seconds = 9.0,
        .device_submit_seconds = 10.0,
        .other_seconds = 11.0,
        .total_seconds = 66.0,
        .measured_calls = 1,
        .snapshot_download_bytes = 101,
        .compaction_download_bytes = 102,
        .compaction_upload_bytes = 103,
        .split_upload_bytes = 104,
    };
    dronegs::TopologyRefinementTelemetry total;
    dronegs::accumulate_topology_telemetry(total, sample);
    dronegs::accumulate_topology_telemetry(total, sample);
    if (dronegs::topology_accounted_seconds(total) != 132.0 ||
        total.total_seconds != 132.0 || total.measured_calls != 2U ||
        total.snapshot_download_bytes != 202U ||
        total.compaction_download_bytes != 204U ||
        total.compaction_upload_bytes != 206U || total.split_upload_bytes != 208U) {
        throw std::runtime_error("topology telemetry aggregation lost a field");
    }
    std::ostringstream json;
    dronegs::write_topology_telemetry(json, total);
    for (const auto* expected : {
             "\"version\":1", "\"timing_basis\":\"host_wall_no_extra_gpu_sync\"",
             "\"scope\":\"process_invocation\"", "\"measured_calls\":2",
             "\"cpu_select_seconds\":16", "\"device_submit_seconds\":20",
             "\"total_seconds\":132", "\"compaction_download_bytes\":204"}) {
        if (json.str().find(expected) == std::string::npos) {
            throw std::runtime_error("topology telemetry JSON contract missing a field");
        }
    }
    std::ostringstream empty;
    dronegs::write_topology_telemetry(empty, {});
    if (empty.str().find("\"measured_calls\":0") == std::string::npos ||
        empty.str().find("\"total_seconds\":0") == std::string::npos) {
        throw std::runtime_error("empty topology telemetry is not zero-initialized");
    }
}

// SPDX-License-Identifier: MIT
#pragma once

#include <cstdint>
#include <iosfwd>

namespace dronegs {

// Host wall time at existing call boundaries, not CUDA kernel elapsed time.
// Invocation-local diagnostics: deliberately absent from checkpoint state.
struct TopologyRefinementTelemetry {
    double host_prepare_seconds = 0.0;
    double snapshot_download_seconds = 0.0;
    double cpu_prune_seconds = 0.0;
    double compaction_cpu_seconds = 0.0;
    double compaction_download_seconds = 0.0;
    double compaction_upload_seconds = 0.0;
    double cpu_score_seconds = 0.0;
    double cpu_select_seconds = 0.0;
    double split_upload_seconds = 0.0;
    double device_submit_seconds = 0.0;
    double other_seconds = 0.0;
    double total_seconds = 0.0;
    std::uint64_t measured_calls = 0;
    std::uint64_t snapshot_download_bytes = 0;
    std::uint64_t compaction_download_bytes = 0;
    std::uint64_t compaction_upload_bytes = 0;
    std::uint64_t split_upload_bytes = 0;
};

double topology_accounted_seconds(const TopologyRefinementTelemetry& value);
void accumulate_topology_telemetry(
    TopologyRefinementTelemetry& total, const TopologyRefinementTelemetry& value);
void write_topology_telemetry(
    std::ostream& stream, const TopologyRefinementTelemetry& value);

}  // namespace dronegs

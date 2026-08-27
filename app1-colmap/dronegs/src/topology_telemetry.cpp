// SPDX-License-Identifier: MIT
#include "dronegs/topology_telemetry.hpp"

#include <ostream>

namespace dronegs {
namespace {
struct TimingField {
    const char* name;
    double TopologyRefinementTelemetry::*member;
};
constexpr TimingField timing_fields[] = {
    {"host_prepare_seconds", &TopologyRefinementTelemetry::host_prepare_seconds},
    {"snapshot_download_seconds", &TopologyRefinementTelemetry::snapshot_download_seconds},
    {"cpu_prune_seconds", &TopologyRefinementTelemetry::cpu_prune_seconds},
    {"compaction_cpu_seconds", &TopologyRefinementTelemetry::compaction_cpu_seconds},
    {"compaction_download_seconds", &TopologyRefinementTelemetry::compaction_download_seconds},
    {"compaction_upload_seconds", &TopologyRefinementTelemetry::compaction_upload_seconds},
    {"cpu_score_seconds", &TopologyRefinementTelemetry::cpu_score_seconds},
    {"cpu_select_seconds", &TopologyRefinementTelemetry::cpu_select_seconds},
    {"split_upload_seconds", &TopologyRefinementTelemetry::split_upload_seconds},
    {"device_submit_seconds", &TopologyRefinementTelemetry::device_submit_seconds},
    {"other_seconds", &TopologyRefinementTelemetry::other_seconds},
};
struct CounterField {
    const char* name;
    std::uint64_t TopologyRefinementTelemetry::*member;
};
constexpr CounterField counter_fields[] = {
    {"measured_calls", &TopologyRefinementTelemetry::measured_calls},
    {"snapshot_download_bytes", &TopologyRefinementTelemetry::snapshot_download_bytes},
    {"compaction_download_bytes", &TopologyRefinementTelemetry::compaction_download_bytes},
    {"compaction_upload_bytes", &TopologyRefinementTelemetry::compaction_upload_bytes},
    {"split_upload_bytes", &TopologyRefinementTelemetry::split_upload_bytes},
};
}  // namespace

double topology_accounted_seconds(const TopologyRefinementTelemetry& value) {
    double result = 0.0;
    for (const auto& field : timing_fields) result += value.*(field.member);
    return result;
}

void accumulate_topology_telemetry(
    TopologyRefinementTelemetry& total, const TopologyRefinementTelemetry& value) {
    for (const auto& field : timing_fields) total.*(field.member) += value.*(field.member);
    for (const auto& field : counter_fields) total.*(field.member) += value.*(field.member);
    total.total_seconds += value.total_seconds;
}

void write_topology_telemetry(
    std::ostream& stream, const TopologyRefinementTelemetry& value) {
    stream << "{\"version\":1,\"timing_basis\":\"host_wall_no_extra_gpu_sync\""
              ",\"scope\":\"process_invocation\"";
    for (const auto& field : timing_fields) {
        stream << ",\"" << field.name << "\":" << value.*(field.member);
    }
    for (const auto& field : counter_fields) {
        stream << ",\"" << field.name << "\":" << value.*(field.member);
    }
    stream << ",\"total_seconds\":" << value.total_seconds << '}';
}
}  // namespace dronegs

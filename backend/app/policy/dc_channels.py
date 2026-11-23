"""dc_agent 双源融合工作流通道定义（策略层 Layer 2）。"""

from typing import Tuple

from app.kernel.run import ChannelSpec, append_list, last_value, merge_dict

SYNTHESIS_CHANNELS: Tuple[ChannelSpec, ...] = (
    ChannelSpec("material_ids", append_list),
    ChannelSpec("clause_ids", append_list),
    ChannelSpec("evidence_ids", append_list),
    ChannelSpec("summary", merge_dict),
    ChannelSpec("status", last_value),
)

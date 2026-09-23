"""请求 / 响应 Pydantic 模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

BeatStatus = Literal["early", "on_time", "late"]


class AlignmentParamsPayload(BaseModel):
    band_width: int = Field(
        default=3, ge=1, le=10000,
        description="Sakoe-Chiba 带宽：允许 |参考索引-演奏索引| 偏离的最大拍数",
    )
    skip_penalty: float | None = Field(
        default=None, ge=0,
        description="漏一拍的代价（秒²）；默认 (0.5×中位拍间隔)²",
    )
    insert_penalty: float | None = Field(
        default=None, ge=0,
        description="插入一拍的代价（秒²）；默认与漏拍代价相同",
    )
    on_time_threshold: float = Field(
        default=0.03, gt=0,
        description="|偏差|≤该秒数判定为准时",
    )


class AnchorPair(BaseModel):
    ref_index: int = Field(
        ..., description="参考拍索引（0 基），锚点表示该参考拍必须与该演奏音匹配"
    )
    perf_index: int = Field(..., description="演奏音索引（0 基）")


class JobCreate(BaseModel):
    name: str | None = Field(default=None, max_length=200, description="任务名称")
    reference_beats: list[float] = Field(
        ..., min_length=1, description="参考节拍网格时间（秒），需严格递增"
    )
    performance_onsets: list[float] = Field(
        ..., min_length=1, description="演奏音符起始时间（秒），需严格递增"
    )
    anchors: list[AnchorPair] | None = Field(
        default=None,
        description=(
            "锚点硬约束（按索引一一对应）：对齐路径必须经过这些 参考拍↔演奏音 匹配点；"
            "参考拍索引与演奏音索引均需严格递增、不越界且在带宽内可达"
        ),
    )
    params: AlignmentParamsPayload = Field(default_factory=AlignmentParamsPayload)

    @field_validator("reference_beats", "performance_onsets")
    @classmethod
    def _strictly_increasing(cls, v: list[float]) -> list[float]:
        # 允许负值（演奏音可能略早于第 0 拍，即前摇），但必须严格递增
        if any(v[i] <= v[i - 1] for i in range(1, len(v))):
            raise ValueError("时间序列必须严格递增")
        return v


class BeatPair(BaseModel):
    ref_index: int
    perf_index: int
    ref_time: float
    perf_time: float
    deviation: float
    status: BeatStatus


class MissedBeat(BaseModel):
    ref_index: int
    ref_time: float


class InsertedBeat(BaseModel):
    perf_index: int
    perf_time: float


class DeviationSegment(BaseModel):
    segment_index: int
    ref_start_index: int
    ref_end_index: int
    ref_start_time: float
    ref_end_time: float
    beat_count: int
    min_deviation: float
    max_deviation: float
    mean_deviation: float
    beats: list[BeatPair]


class SignedInterval(BaseModel):
    segment_index: int
    status: BeatStatus
    ref_start_index: int
    ref_end_index: int
    ref_start_time: float
    ref_end_time: float
    beat_count: int
    min_deviation: float
    max_deviation: float
    mean_deviation: float


class AdoptedAnchor(BaseModel):
    ref_index: int
    perf_index: int
    ref_time: float
    perf_time: float
    deviation: float
    status: BeatStatus


class AlignmentResult(BaseModel):
    parameters: dict[str, Any]
    anchors: list[AdoptedAnchor] = Field(
        default_factory=list,
        description="本次对齐实际采用的锚点（与请求中给出的硬约束一致）",
    )
    matched_beats: list[BeatPair]
    missed_beats: list[MissedBeat]
    inserted_beats: list[InsertedBeat]
    deviation_segments: list[DeviationSegment]
    signed_intervals: list[SignedInterval]
    statistics: dict[str, Any]


class TempoEvent(BaseModel):
    ref_start_index: int = Field(..., description="事件起拍：第一个匹配参考拍的 0 基索引")
    ref_end_index: int = Field(
        ..., description="事件结束拍（含）：区间内最后一个匹配参考拍的 0 基索引"
    )
    start_time: float = Field(..., description="事件起拍对应的演奏时间（秒）")
    ref_time: float = Field(..., description="事件起拍对应的参考时间（秒）")
    bpm: float = Field(..., description="该稳定区间的估计 BPM（区间内稳健 BPM 的中位数）")
    beat_count: int = Field(
        ...,
        description=(
            "覆盖拍数（ref_end_index − ref_start_index），被忽略间隔跨过的漏拍也计入；"
            "相邻事件共享边界拍"
        ),
    )
    gap_count: int = Field(
        ..., description="并入本事件的相邻匹配间隔数（被忽略的异常间隔不计）"
    )


class TempoMapOut(BaseModel):
    job_id: int
    parameters: dict[str, Any]
    matched_beat_count: int = Field(..., description="本次估计使用的匹配拍数")
    events: list[TempoEvent] = Field(
        default_factory=list,
        description="按参考拍排序的 tempo events（稳定 BPM 区间，自该事件起拍生效）",
    )
    ignored_ref_indices: list[int] = Field(
        default_factory=list,
        description="被忽略的异常间隔起始参考拍索引（漏拍 / 插入拍残留或超 BPM 范围）",
    )


ReviewIssueType = Literal["missed", "inserted", "deviation"]


class ReviewIssue(BaseModel):
    type: ReviewIssueType = Field(
        ..., description="问题类型：missed=漏拍，inserted=插入音，deviation=超阈值偏差"
    )
    ref_index: int | None = Field(
        ..., description="关联的参考拍 0 基索引（插入音无对应参考拍，为 null）"
    )
    beat_number: int | None = Field(
        ..., description="关联的 1 基拍号（插入音为 null）"
    )
    perf_index: int | None = Field(..., description="关联的演奏音 0 基索引（漏拍为 null）")
    ref_time: float | None = Field(..., description="参考时间（秒）；插入音为 null")
    perf_time: float | None = Field(..., description="演奏时间（秒）；漏拍为 null")
    deviation: float | None = Field(
        ..., description="偏差（秒，perf−ref）；仅超阈值偏差问题给出"
    )
    status: BeatStatus | None = Field(
        ..., description="early/late；仅超阈值偏差问题给出"
    )
    description: str = Field(..., description="单问题的中文描述")


class ReviewSegment(BaseModel):
    segment_index: int
    ref_start_index: int = Field(..., description="片段起始参考拍 0 基索引")
    ref_end_index: int = Field(..., description="片段结束参考拍 0 基索引（含）")
    beat_number_start: int = Field(..., description="片段起始拍号（1 基，含）")
    beat_number_end: int = Field(..., description="片段结束拍号（1 基，含）")
    ref_start_time: float | None
    ref_end_time: float | None
    missed_count: int
    inserted_count: int
    over_threshold_count: int = Field(..., description="超阈值偏差拍数")
    early_count: int
    late_count: int
    issue_count: int
    max_abs_deviation: float | None
    summary: str = Field(..., description="问题摘要（中文）")
    issues: list[ReviewIssue]


class ReviewSegmentsOut(BaseModel):
    job_id: int
    parameters: dict[str, Any]
    segment_count: int = Field(..., description="复核片段数量")
    total_missed_count: int
    total_inserted_count: int
    total_over_threshold_count: int
    segments: list[ReviewSegment] = Field(
        default_factory=list, description="按参考拍顺序排列的复核片段"
    )


class ConsensusRequest(BaseModel):
    job_ids: list[int] = Field(
        ..., min_length=3,
        description="参与合奏一致性分析的已完成任务ID（至少 3 个，不得重复）",
    )
    deviation_threshold: float = Field(
        default=0.05, gt=0,
        description=(
            "异常阈值（秒）：|校正时间 − 合奏基准| 严格大于该值的任务列为异常"
        ),
    )

    @field_validator("job_ids")
    @classmethod
    def _unique_job_ids(cls, v: list[int]) -> list[int]:
        if len(set(v)) != len(v):
            raise ValueError("任务ID不得重复")
        return v


class ConsensusMissedBeat(BaseModel):
    ref_index: int
    ref_time: float


class ConsensusTrack(BaseModel):
    job_id: int
    latency: float | None = Field(
        ...,
        description=(
            "固定录音延迟估计（秒）：该轨匹配拍 perf_time−ref_time 的中位数，"
            "已从演奏时间中扣除；无匹配拍时为 null"
        ),
    )
    matched_count: int = Field(..., description="该轨的匹配拍数")
    missed_beats: list[ConsensusMissedBeat] = Field(
        default_factory=list, description="该轨的漏拍（参考拍索引与时间）"
    )


class ConsensusBeatEntry(BaseModel):
    job_id: int
    corrected_time: float = Field(
        ..., description="扣除该轨固定延迟后的演奏时间（秒）"
    )
    offset: float | None = Field(
        ..., description="相对合奏基准的带符号偏差（秒）；拍不可判定时为 null"
    )


class ConsensusAnomaly(BaseModel):
    job_id: int
    deviation: float = Field(
        ..., description="相对合奏基准的带符号偏差（秒），绝对值严格大于阈值"
    )


class ConsensusBeat(BaseModel):
    ref_index: int
    ref_time: float
    covered_count: int = Field(..., description="该参考拍上有匹配拍的轨数")
    decidable: bool = Field(
        ..., description="覆盖轨数达到 min_tracks_per_beat 才可判定；否则不报异常"
    )
    baseline: float | None = Field(
        ..., description="合奏基准（各轨校正时间的中位数，秒）；不可判定时为 null"
    )
    entries: list[ConsensusBeatEntry] = Field(
        default_factory=list, description="各轨在该拍的校正时间与相对基准偏差"
    )
    anomalies: list[ConsensusAnomaly] = Field(
        default_factory=list, description="偏离基准超过阈值的任务（仅可判定时给出）"
    )


class ConsensusOut(BaseModel):
    job_ids: list[int]
    parameters: dict[str, Any]
    ref_beat_count: int
    decidable_beat_count: int
    undecidable_beat_count: int
    anomaly_count: int = Field(..., description="全部可判定拍上的异常总数")
    tracks: list[ConsensusTrack]
    beats: list[ConsensusBeat]


class JobSummary(BaseModel):
    id: int
    name: str | None
    status: str
    created_at: str
    aligned_at: str | None
    ref_beat_count: int
    perf_onset_count: int


class JobOut(BaseModel):
    id: int
    name: str | None
    reference_beats: list[float]
    performance_onsets: list[float]
    anchors: list[AnchorPair] = Field(
        default_factory=list, description="任务创建时指定的锚点硬约束"
    )
    params: dict[str, Any]
    status: str
    error: str | None
    created_at: str
    aligned_at: str | None
    result: AlignmentResult | None = None

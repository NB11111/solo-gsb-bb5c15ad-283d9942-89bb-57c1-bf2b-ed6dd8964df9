"""跨任务合奏一致性：共享参考网格的多轨延迟校正、逐拍中位数基准与异常检测。

多个已完成任务使用**完全相同的参考节拍网格**时，可把每条演奏轨视为同一
合奏的一次录音。每条轨可能存在固定的录音延迟（设备 / 起录偏移），处理
流程：

1. 对每轨，以其匹配拍的 ``perf_time − ref_time`` **中位数**估计固定录音
   延迟，并从演奏时间中扣除，得到校正时间（常数偏移因此被吸收，只有
   相对合奏的局部偏离才会暴露）；
2. 按参考拍汇总各轨校正时间：覆盖轨数达到 ``MIN_TRACKS_PER_BEAT`` 的拍
   取中位数作为**合奏基准**，逐轨比较，``|校正时间 − 基准|`` **严格大于**
   异常阈值的任务被列为异常（带符号偏差）；
3. 覆盖不足的拍标记为**不可判定**（基准为 null），不报异常；漏拍按轨
   列出。

所有时间量保留 6 位小数（微秒级），延迟、校正时间、基准、偏差逐级
取整，响应中的每个数值都可由响应自身复算。
"""

from __future__ import annotations

from typing import Any, Sequence

_DECIMALS = 6
# 一个参考拍至少被该数量的轨覆盖时才可判定（取中位数基准）
MIN_TRACKS_PER_BEAT = 3


class EnsembleError(ValueError):
    """合奏一致性分析的输入不满足计算条件。"""


def _median(values: Sequence[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return s[mid]
    return 0.5 * (s[mid - 1] + s[mid])


def build_consensus(
    jobs: Sequence[dict[str, Any]],
    deviation_threshold: float = 0.05,
    min_tracks_per_beat: int = MIN_TRACKS_PER_BEAT,
) -> dict[str, Any]:
    """对多个已完成任务做跨任务合奏一致性分析。

    ``jobs`` 为任务字典（至少含 ``id`` / ``reference_beats`` / ``result``，
    与 ``crud.get_job`` 的返回结构一致）。要求任务数不少于
    ``min_tracks_per_beat``、ID 不重复且参考节拍网格完全相同，否则抛出
    ``EnsembleError``。返回每轨延迟与漏拍、逐拍基准 / 异常及汇总计数。
    """
    if deviation_threshold <= 0:
        raise EnsembleError("异常阈值 deviation_threshold 必须为正数")
    if min_tracks_per_beat < 2:
        raise EnsembleError("min_tracks_per_beat 至少为 2")
    if len(jobs) < min_tracks_per_beat:
        raise EnsembleError(
            f"至少需要 {min_tracks_per_beat} 个任务，实际收到 {len(jobs)} 个"
        )
    job_ids = [j["id"] for j in jobs]
    if len(set(job_ids)) != len(job_ids):
        raise EnsembleError("任务ID不得重复")

    reference = list(jobs[0]["reference_beats"])
    for job in jobs[1:]:
        if list(job["reference_beats"]) != reference:
            raise EnsembleError(
                f"任务 {job['id']} 的参考节拍网格与任务 {job_ids[0]} 不一致："
                "合奏一致性分析仅接受完全相同的参考节拍网格"
            )

    # ---- 1. 逐轨估计固定录音延迟（匹配拍偏差中位数）并校正演奏时间 ----
    tracks: list[dict[str, Any]] = []
    # 每个参考拍 -> [(job_id, corrected_time)]，按任务提交顺序追加
    beat_entries: list[list[tuple[int, float]]] = [[] for _ in reference]
    for job in jobs:
        result = job.get("result") or {}
        matched = result.get("matched_beats") or []
        missed = result.get("missed_beats") or []
        deviations = [
            round(b["perf_time"] - b["ref_time"], _DECIMALS) for b in matched
        ]
        # 无匹配拍的轨无法估计延迟：latency 为 None，不参与任何拍的判定
        latency = (
            round(_median(deviations), _DECIMALS) if deviations else None
        )
        tracks.append({
            "job_id": job["id"],
            "latency": latency,
            "matched_count": len(matched),
            "missed_beats": [
                {"ref_index": b["ref_index"], "ref_time": b["ref_time"]}
                for b in sorted(missed, key=lambda b: b["ref_index"])
            ],
        })
        if latency is None:
            continue
        for b in matched:
            ri = b["ref_index"]
            if 0 <= ri < len(reference):
                corrected = round(b["perf_time"] - latency, _DECIMALS)
                beat_entries[ri].append((job["id"], corrected))

    # ---- 2. 按参考拍汇总：覆盖足够时取中位数为合奏基准并列出异常 ----
    beats: list[dict[str, Any]] = []
    for ri, ref_time in enumerate(reference):
        entries = beat_entries[ri]
        covered = len(entries)
        decidable = covered >= min_tracks_per_beat
        baseline = (
            round(_median([t for _, t in entries]), _DECIMALS)
            if decidable
            else None
        )
        entry_out: list[dict[str, Any]] = []
        anomalies: list[dict[str, Any]] = []
        for job_id, corrected in entries:
            offset = (
                round(corrected - baseline, _DECIMALS)
                if baseline is not None
                else None
            )
            entry_out.append({
                "job_id": job_id,
                "corrected_time": corrected,
                "offset": offset,
            })
            # 覆盖不足的拍不判定；判定时 |偏差| 严格大于阈值才算异常
            if offset is not None and abs(offset) > deviation_threshold:
                anomalies.append({"job_id": job_id, "deviation": offset})
        beats.append({
            "ref_index": ri,
            "ref_time": round(ref_time, _DECIMALS),
            "covered_count": covered,
            "decidable": decidable,
            "baseline": baseline,
            "entries": entry_out,
            "anomalies": anomalies,
        })

    return {
        "job_ids": job_ids,
        "parameters": {
            "deviation_threshold": deviation_threshold,
            "min_tracks_per_beat": min_tracks_per_beat,
        },
        "ref_beat_count": len(reference),
        "decidable_beat_count": sum(1 for b in beats if b["decidable"]),
        "undecidable_beat_count": sum(1 for b in beats if not b["decidable"]),
        "anomaly_count": sum(len(b["anomalies"]) for b in beats),
        "tracks": tracks,
        "beats": beats,
    }

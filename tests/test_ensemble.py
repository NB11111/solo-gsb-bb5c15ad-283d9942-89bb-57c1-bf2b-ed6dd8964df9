"""跨任务合奏一致性（build_consensus）单元测试。"""

import unittest

from app.ensemble import EnsembleError, build_consensus

REF = [0.0, 0.5, 1.0, 1.5, 2.0]


def _matched(pairs):
    """pairs: [(ref_index, perf_index, perf_time)]，ref_time 取自 REF。"""
    return [
        {
            "ref_index": ri,
            "perf_index": pi,
            "ref_time": REF[ri],
            "perf_time": pt,
            "deviation": round(pt - REF[ri], 6),
            "status": "on_time",
        }
        for ri, pi, pt in pairs
    ]


def _job(job_id, matched, missed=None, ref=REF):
    return {
        "id": job_id,
        "reference_beats": list(ref),
        "result": {
            "matched_beats": matched,
            "missed_beats": missed or [],
        },
    }


def _on_time_job(job_id, offset=0.0):
    return _job(
        job_id,
        _matched([(i, i, t + offset) for i, t in enumerate(REF)]),
    )


class TestConsensus(unittest.TestCase):
    def test_latency_estimated_and_corrected(self):
        # 三条轨分别带 0 / +0.1 / -0.02 的固定录音延迟，校正后完全对齐
        jobs = [
            _on_time_job(1),
            _on_time_job(2, offset=0.1),
            _on_time_job(3, offset=-0.02),
        ]
        out = build_consensus(jobs, deviation_threshold=0.05)
        latencies = {t["job_id"]: t["latency"] for t in out["tracks"]}
        self.assertEqual(latencies, {1: 0.0, 2: 0.1, 3: -0.02})
        for beat in out["beats"]:
            self.assertTrue(beat["decidable"])
            self.assertEqual(beat["covered_count"], 3)
            self.assertAlmostEqual(
                beat["baseline"], REF[beat["ref_index"]], places=6
            )
            self.assertEqual(beat["anomalies"], [])
            for entry in beat["entries"]:
                self.assertAlmostEqual(entry["offset"], 0.0, places=6)
        self.assertEqual(out["anomaly_count"], 0)
        self.assertEqual(out["decidable_beat_count"], len(REF))
        self.assertEqual(out["undecidable_beat_count"], 0)

    def test_baseline_median_and_anomaly(self):
        # 轨 3 在第 4 拍（索引 3）拖拍 0.2s，中位数基准不受离群值影响
        jobs = [
            _on_time_job(1),
            _on_time_job(2),
            _job(3, _matched([
                (0, 0, 0.0), (1, 1, 0.5), (2, 2, 1.0), (3, 3, 1.7), (4, 4, 2.0),
            ])),
        ]
        out = build_consensus(jobs, deviation_threshold=0.05)
        beat3 = out["beats"][3]
        self.assertEqual(beat3["baseline"], 1.5)
        self.assertEqual(beat3["anomalies"], [{"job_id": 3, "deviation": 0.2}])
        offsets = {e["job_id"]: e["offset"] for e in beat3["entries"]}
        self.assertEqual(offsets, {1: 0.0, 2: 0.0, 3: 0.2})
        self.assertEqual(out["anomaly_count"], 1)
        for beat in out["beats"]:
            if beat["ref_index"] != 3:
                self.assertEqual(beat["anomalies"], [])

    def test_threshold_is_strict(self):
        # 偏差恰好等于阈值不算异常（严格大于才报）
        jobs = [
            _on_time_job(1),
            _on_time_job(2),
            _job(3, _matched([
                (0, 0, 0.0), (1, 1, 0.55), (2, 2, 1.0), (3, 3, 1.5), (4, 4, 2.0),
            ])),
        ]
        out = build_consensus(jobs, deviation_threshold=0.05)
        beat1 = out["beats"][1]
        entry3 = [e for e in beat1["entries"] if e["job_id"] == 3][0]
        self.assertEqual(entry3["offset"], 0.05)
        self.assertEqual(beat1["anomalies"], [])
        self.assertEqual(out["anomaly_count"], 0)

    def test_undecidable_beat_reports_no_anomaly(self):
        # 轨 3 漏第 3 拍（索引 2）-> 该拍仅 2 轨覆盖，不可判定；
        # 即使两轨校正时间相差 0.4s 也不报异常
        jobs = [
            _on_time_job(1),
            _job(2, _matched([
                (0, 0, 0.0), (1, 1, 0.5), (2, 2, 1.4), (3, 3, 1.5), (4, 4, 2.0),
            ])),
            _job(
                3,
                _matched([(0, 0, 0.0), (1, 1, 0.5), (3, 2, 1.5), (4, 3, 2.0)]),
                missed=[{"ref_index": 2, "ref_time": 1.0}],
            ),
        ]
        out = build_consensus(jobs, deviation_threshold=0.05)
        beat2 = out["beats"][2]
        self.assertEqual(beat2["covered_count"], 2)
        self.assertFalse(beat2["decidable"])
        self.assertIsNone(beat2["baseline"])
        self.assertEqual(beat2["anomalies"], [])
        self.assertTrue(all(e["offset"] is None for e in beat2["entries"]))
        self.assertEqual(out["anomaly_count"], 0)
        self.assertEqual(out["undecidable_beat_count"], 1)
        self.assertEqual(out["decidable_beat_count"], len(REF) - 1)

    def test_missed_beats_listed_per_track(self):
        jobs = [
            _on_time_job(1),
            _on_time_job(2),
            _job(
                3,
                _matched([(0, 0, 0.0), (2, 1, 1.0), (4, 2, 2.0)]),
                missed=[
                    {"ref_index": 3, "ref_time": 1.5},
                    {"ref_index": 1, "ref_time": 0.5},
                ],
            ),
        ]
        out = build_consensus(jobs, deviation_threshold=0.05)
        missed = {t["job_id"]: t["missed_beats"] for t in out["tracks"]}
        self.assertEqual(missed[1], [])
        self.assertEqual(missed[2], [])
        # 按参考拍索引排序输出
        self.assertEqual(
            missed[3],
            [
                {"ref_index": 1, "ref_time": 0.5},
                {"ref_index": 3, "ref_time": 1.5},
            ],
        )

    def test_even_track_count_median_and_signed_anomalies(self):
        # 4 轨：基准取中间两个校正时间的平均；首拍 ±0.04 的两轨越限
        def jittered(job_id, first_beat):
            times = [first_beat, 0.5, 1.0, 1.5, 2.0]
            return _job(job_id, _matched(list(zip(range(5), range(5), times))))

        jobs = [
            jittered(1, -0.04),
            jittered(2, -0.02),
            jittered(3, 0.02),
            jittered(4, 0.04),
        ]
        out = build_consensus(jobs, deviation_threshold=0.03)
        beat0 = out["beats"][0]
        self.assertEqual(beat0["covered_count"], 4)
        self.assertEqual(beat0["baseline"], 0.0)  # (-0.02 + 0.02) / 2
        self.assertEqual(
            beat0["anomalies"],
            [{"job_id": 1, "deviation": -0.04}, {"job_id": 4, "deviation": 0.04}],
        )
        self.assertEqual(out["anomaly_count"], 2)

    def test_track_without_matched_beats_has_null_latency(self):
        jobs = [
            _on_time_job(1),
            _on_time_job(2),
            _job(
                3,
                [],
                missed=[{"ref_index": i, "ref_time": t}
                        for i, t in enumerate(REF)],
            ),
        ]
        out = build_consensus(jobs, deviation_threshold=0.05)
        track3 = [t for t in out["tracks"] if t["job_id"] == 3][0]
        self.assertIsNone(track3["latency"])
        self.assertEqual(track3["matched_count"], 0)
        self.assertEqual(len(track3["missed_beats"]), len(REF))
        # 该轨不参与任何拍：所有拍只有 2 轨覆盖，全部不可判定
        self.assertEqual(out["decidable_beat_count"], 0)
        self.assertEqual(out["anomaly_count"], 0)

    def test_grid_mismatch_rejected(self):
        jobs = [_on_time_job(1), _on_time_job(2),
                _on_time_job(3), ]
        # 时间值不同
        jobs[2] = _job(3, _matched([(0, 0, 0.0)]),
                       ref=[0.0, 0.5, 1.0, 1.5, 2.5])
        with self.assertRaises(EnsembleError):
            build_consensus(jobs, deviation_threshold=0.05)
        # 长度不同
        jobs[2] = _job(3, _matched([(0, 0, 0.0)]), ref=REF + [2.5])
        with self.assertRaises(EnsembleError):
            build_consensus(jobs, deviation_threshold=0.05)

    def test_duplicate_ids_rejected(self):
        jobs = [_on_time_job(1), _on_time_job(2), _on_time_job(1)]
        with self.assertRaises(EnsembleError):
            build_consensus(jobs, deviation_threshold=0.05)

    def test_too_few_jobs_rejected(self):
        with self.assertRaises(EnsembleError):
            build_consensus([_on_time_job(1), _on_time_job(2)],
                            deviation_threshold=0.05)

    def test_threshold_must_be_positive(self):
        jobs = [_on_time_job(1), _on_time_job(2), _on_time_job(3)]
        with self.assertRaises(EnsembleError):
            build_consensus(jobs, deviation_threshold=0.0)
        with self.assertRaises(EnsembleError):
            build_consensus(jobs, deviation_threshold=-0.1)


if __name__ == "__main__":
    unittest.main()

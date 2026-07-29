import unittest
import tempfile
from pathlib import Path

import sqr_runtime


class SqrRuntimeTests(unittest.TestCase):
    def test_passthrough_segment_requires_explicit_mode_or_flag(self):
        self.assertTrue(
            sqr_runtime.is_passthrough_segment({"mode": "passthrough"})
        )
        self.assertTrue(
            sqr_runtime.is_passthrough_segment({"skip_sampling": True})
        )
        self.assertFalse(
            sqr_runtime.is_passthrough_segment(
                {"mode": "transfer", "positive": ""}
            )
        )

    def test_prune_prompt_keeps_only_transitive_dependencies(self):
        prompt = {
            "load": {"class_type": "VHS_LoadVideo", "inputs": {}},
            "combine": {
                "class_type": "VHS_VideoCombine",
                "inputs": {"images": ["load", 0]},
            },
            "sampler": {"class_type": "KSampler", "inputs": {}},
            "preview": {
                "class_type": "PreviewImage",
                "inputs": {"images": ["sampler", 0]},
            },
        }

        pruned = sqr_runtime.prune_prompt_to_outputs(prompt, ["combine"])

        self.assertEqual(set(pruned), {"load", "combine"})

    def test_remove_managed_path_is_prefix_and_root_scoped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            managed = Path(temp_dir, "sqr_trans_segment.mp4")
            unrelated = Path(temp_dir, "user_video.mp4")
            managed.touch()
            unrelated.touch()

            self.assertTrue(
                sqr_runtime.remove_managed_path(
                    str(managed),
                    [temp_dir],
                    ["sqr_trans_"],
                )
            )
            self.assertFalse(
                sqr_runtime.remove_managed_path(
                    str(unrelated),
                    [temp_dir],
                    ["sqr_trans_"],
                )
            )
            self.assertTrue(unrelated.exists())

    def test_wan_model_length_uses_4n_plus_1_alignment(self):
        self.assertEqual(sqr_runtime.wan_model_length(60), 61)
        self.assertEqual(sqr_runtime.wan_model_length(65), 65)
        self.assertEqual(sqr_runtime.wan_model_length(81), 81)

    def test_long_director_segment_is_balanced_for_transition_context(self):
        plan = [
            (
                0,
                301,
                {
                    "id": "shot-a",
                    "visible_length": 300,
                    "mode": "transfer",
                    "references": [{"path": "person.png"}],
                },
            )
        ]

        expanded = sqr_runtime.expand_director_plan_for_context(
            plan,
            transition_enabled=True,
        )

        self.assertEqual(len(expanded), 5)
        self.assertEqual([item[0] for item in expanded], [0, 60, 120, 180, 240])
        self.assertEqual(
            [item[2]["visible_length"] for item in expanded],
            [60, 60, 60, 60, 60],
        )
        self.assertTrue(all(item[1] + 16 <= 81 for item in expanded))
        self.assertTrue(
            all(
                item[2]["references"] == [{"path": "person.png"}]
                for item in expanded
            )
        )

    def test_453_frames_expand_to_seven_quality_chunks(self):
        plan = [
            (
                0,
                453,
                {
                    "id": "long-shot",
                    "visible_length": 453,
                    "mode": "transfer",
                },
            )
        ]

        expanded = sqr_runtime.expand_director_plan_for_context(
            plan,
            transition_enabled=True,
        )

        self.assertEqual(len(expanded), 7)
        self.assertEqual(sum(x[2]["visible_length"] for x in expanded), 453)
        self.assertLessEqual(
            max(x[2]["visible_length"] for x in expanded),
            65,
        )
        self.assertEqual(expanded[-1][2]["end"], 453)

    def test_passthrough_segment_is_not_micro_split(self):
        plan = [
            (
                10,
                201,
                {
                    "id": "source-cut",
                    "visible_length": 200,
                    "mode": "passthrough",
                },
            )
        ]

        expanded = sqr_runtime.expand_director_plan_for_context(
            plan,
            transition_enabled=True,
        )

        self.assertEqual(len(expanded), 1)
        self.assertEqual(expanded[0][0], 10)
        self.assertEqual(expanded[0][2]["visible_length"], 200)


if __name__ == "__main__":
    unittest.main()

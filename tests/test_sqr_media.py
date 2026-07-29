import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import sqr_media


class SqrMediaTests(unittest.TestCase):
    def test_resolve_ffmpeg_prefers_vhs_forced_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir, "ffmpeg")
            executable.touch()
            with mock.patch.dict(
                os.environ,
                {"VHS_FORCE_FFMPEG_PATH": str(executable)},
                clear=False,
            ):
                self.assertEqual(
                    sqr_media.resolve_ffmpeg_path(),
                    os.path.abspath(executable),
                )

    def test_resolve_ffmpeg_accepts_vhs_forced_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir, "ffmpeg")
            executable.touch()
            with mock.patch.dict(
                os.environ,
                {"VHS_FORCE_FFMPEG_PATH": temp_dir},
                clear=False,
            ):
                self.assertEqual(
                    sqr_media.resolve_ffmpeg_path(),
                    os.path.abspath(executable),
                )

    def test_probe_audio_stream_uses_ffprobe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir, "source.mp4")
            ffprobe = Path(temp_dir, "ffprobe")
            media.touch()
            ffprobe.touch()
            commands = []

            def runner(command, **kwargs):
                commands.append(command)
                return SimpleNamespace(returncode=0, stdout="0\n", stderr="")

            has_audio, detail = sqr_media.probe_audio_stream(
                str(media),
                ffprobe_path=str(ffprobe),
                runner=runner,
            )

            self.assertTrue(has_audio)
            self.assertEqual(detail, "ffprobe")
            self.assertEqual(commands[0][0], os.path.abspath(ffprobe))

    def test_probe_failure_is_unknown_instead_of_silent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir, "source.mp4")
            media.touch()
            with (
                mock.patch.object(
                    sqr_media, "resolve_ffmpeg_path", return_value=None
                ),
                mock.patch.object(
                    sqr_media, "resolve_ffprobe_path", return_value=None
                ),
            ):
                has_audio, detail = sqr_media.probe_audio_stream(str(media))

            self.assertIsNone(has_audio)
            self.assertIn("unavailable", detail)

    def test_merge_uses_resolved_ffmpeg_and_required_audio_map(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ffmpeg = Path(temp_dir, "ffmpeg")
            first = Path(temp_dir, "first.mp4")
            second = Path(temp_dir, "second.mp4")
            source = Path(temp_dir, "source.mp4")
            output = Path(temp_dir, "merged.mp4")
            for path in (ffmpeg, first, second, source, output):
                path.touch()

            commands = []
            logs = []

            def runner(command, **kwargs):
                commands.append(command)
                if "ffprobe" in os.path.basename(command[0]):
                    return SimpleNamespace(returncode=0, stdout="0\n", stderr="")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            result = sqr_media.merge_videos(
                [str(first), str(second)],
                str(output),
                source_audio_path=str(source),
                total_frames=600,
                source_fps=30,
                ffmpeg_path=str(ffmpeg),
                runner=runner,
                logger=logs.append,
            )

            self.assertTrue(result)
            self.assertEqual(len(commands), 3)
            self.assertTrue(
                all(
                    command[0] == os.path.abspath(ffmpeg)
                    for command in commands[:2]
                )
            )
            self.assertIn("1:a:0", commands[-2])
            self.assertNotIn("1:a:0?", commands[-2])
            self.assertTrue(any("音轨校验通过" in line for line in logs))

    def test_merge_fails_closed_when_final_audio_validation_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ffmpeg = Path(temp_dir, "ffmpeg")
            ffprobe = Path(temp_dir, "ffprobe")
            segment = Path(temp_dir, "segment.mp4")
            source = Path(temp_dir, "source.mp4")
            output = Path(temp_dir, "merged.mp4")
            for path in (ffmpeg, ffprobe, segment, source, output):
                path.touch()
            logs = []

            def runner(command, **kwargs):
                if os.path.basename(command[0]) == "ffprobe":
                    return SimpleNamespace(
                        returncode=0,
                        stdout="",
                        stderr="",
                    )
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            result = sqr_media.merge_videos(
                [str(segment)],
                str(output),
                source_audio_path=str(source),
                total_frames=10,
                source_fps=10,
                ffmpeg_path=str(ffmpeg),
                runner=runner,
                logger=logs.append,
            )

            self.assertFalse(result)
            self.assertTrue(
                any("音轨校验失败" in line for line in logs)
            )

    def test_preview_record_is_relative_to_output_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir, "output")
            video = output_root / "2026-07-29" / "merged.mp4"
            video.parent.mkdir(parents=True)
            video.touch()

            preview = sqr_media.build_video_preview(
                str(video),
                str(output_root),
                30,
            )

            self.assertEqual(preview["filename"], "merged.mp4")
            self.assertEqual(preview["subfolder"], "2026-07-29")
            self.assertEqual(preview["type"], "output")
            self.assertEqual(preview["frame_rate"], 30.0)


if __name__ == "__main__":
    unittest.main()

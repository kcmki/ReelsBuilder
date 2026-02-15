import os
import subprocess
import traceback
from typing import List
from pathlib import Path

from moviepy.editor import (
    VideoFileClip,
    CompositeVideoClip,
    ImageClip,
    vfx,
)
from skimage.filters import gaussian


class ClipBuilder:
    def __init__(
        self,
        temp_video_path: str = "temp/video.mp4",
        logo_path: str | None = None,
    ):
        self.temp_video_path = Path(temp_video_path)
        self.logo_path = Path(logo_path) if logo_path else None
        self.temp_video_path.parent.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------
    # CLIP BUILDING
    # ---------------------------------------------------
    def build_clips(
        self,
        points: List[dict],
        output_dir: str = "clips",
        min_duration: int = 20,
    ) -> List[str]:
        """
        Cut clips from a downloaded video using ffmpeg.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        generated_clips = []

        for idx, point in enumerate(points):
            start_time = int(point["startMillis"]) / 1000
            duration = max(int(point["durationMillis"]) / 1000, min_duration)

            output_file = output_dir / f"clip_{idx + 1}.mp4"

            command = [
                "ffmpeg",
                "-ss", str(start_time),
                "-i", str(self.temp_video_path),
                "-t", str(duration),
                "-c:v", "libx264",
                "-c:a", "aac",
                "-y",
                str(output_file),
            ]

            subprocess.run(command, check=True)
            generated_clips.append(str(output_file))

        return generated_clips

    # ---------------------------------------------------
    # BLUR UTIL
    # ---------------------------------------------------
    @staticmethod
    def blur(gf, t):
        image = gf(t)
        return gaussian(image.astype(float), sigma=2)

    # ---------------------------------------------------
    # CONVERT TO REELS
    # ---------------------------------------------------
    def convert_to_reels(
        self,
        input_clip: str,
        output_clip: str,
        target_height: int = 480,
    ) -> None:
        clip = VideoFileClip(input_clip)
        width, height = clip.size

        crop_width = int(width * 0.10)
        crop_height = int(height * 0.10)

        clip = clip.with_effects([
            vfx.Crop(
                x1=0,
                y1=0,
                x2=width - crop_width,
                y2=height - crop_height,
            )
        ])

        if clip.size[0] <= clip.size[1]:
            clip.write_videofile(
                output_clip,
                codec="libx264",
                audio_codec="aac",
                preset="ultrafast",
            )
            return

        # Background
        new_width = clip.size[1] * target_height // clip.size[0]
        background = clip.with_effects([
            vfx.Resize(height=target_height),
            vfx.Crop(x_center=clip.size[0] // 2, width=new_width),
        ]).transform(self.blur)

        # Main video
        main = clip.with_effects([
            vfx.Resize(width=new_width)
        ])

        layers = [background, main.with_position("center")]

        if self.logo_path and self.logo_path.exists():
            logo = (
                ImageClip(str(self.logo_path))
                .with_duration(clip.duration)
                .with_effects([vfx.Resize(height=50), vfx.Margin(bottom=10, opacity=0)])
                .with_position(("center", "bottom"))
            )
            layers.append(logo)

        final = CompositeVideoClip(layers)
        final.write_videofile(
            output_clip,
            codec="libx264",
            audio_codec="aac",
            threads=4,
            preset="ultrafast",
        )

    # ---------------------------------------------------
    # FULL PIPELINE
    # ---------------------------------------------------
    def build_reels_from_points(
        self,
        points: List[dict],
        clips_dir: str,
        reels_dir: str,
        retries: int = 3,
    ) -> None:
        clips_dir = Path(clips_dir)
        reels_dir = Path(reels_dir)

        clips_dir.mkdir(parents=True, exist_ok=True)
        reels_dir.mkdir(parents=True, exist_ok=True)

        for _ in range(retries):
            try:
                clips = self.build_clips(points, output_dir=clips_dir)

                for clip_path in clips:
                    output_clip = reels_dir / f"reels_{Path(clip_path).name}"
                    self.convert_to_reels(clip_path, str(output_clip))

                break
            except Exception:
                traceback.print_exc()

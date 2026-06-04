import os
import subprocess
import traceback
from typing import List
from pathlib import Path

from moviepy import (
    VideoFileClip,
    CompositeVideoClip,
    ImageClip,
    vfx
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
        Cut clips from a downloaded video using ffmpeg, ensuring no overlaps.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        generated_clips = []
        
        # Sort points by start time to handle overlap detection linearly
        sorted_points = sorted(points, key=lambda x: int(x.get("startMillis", 0)))
        
        last_end_time = 0.0

        for idx, point in enumerate(sorted_points):
            intended_start = int(point["startMillis"]) / 1000
            duration = max(int(point["durationMillis"]) / 1000, min_duration)
            
            # Ensure we don't start before the previous clip ended
            start_time = max(intended_start, last_end_time)
            
            # If the intended start was adjusted, we should check if there's still room for a clip
            # or if we should just shift the duration. 
            # To keep it simple and ensure no overlap:
            end_time = start_time + duration
            
            output_file = output_dir / f"clip_{idx + 1}.mp4"

            print(f"Generating clip {idx+1}: start={start_time}s, duration={duration}s (intended start was {intended_start}s)")

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

            try:
                subprocess.run(command, check=True, capture_output=True)
                generated_clips.append(str(output_file))
                last_end_time = end_time
            except subprocess.CalledProcessError as e:
                print(f"Error generating clip {idx+1}: {e.stderr.decode()}")
                continue

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
    def blur(self,gf,t):
        """ Returns a blurred (radius=2 pixels) version of the image """
        image = gf(t)
        return gaussian(image.astype(float), sigma=2)

    
    def convert_to_reels(
        self,
        input_clip: str,
        output_clip: str,
        height: int = 480,
    ) -> None:
        clip = VideoFileClip(input_clip)
        width, height = clip.size

        # Calculate the crop dimensions
        crop_width = int(width * 0.10)   # 5% from the right
        crop_height = int(height * 0.10) # 5% from the bottom

        # Apply the crop effect
        # remove 5% from each side of the video to remove watermarks/logos
        clip = clip.with_effects([
            vfx.Crop(
                x1=0,#crop_width,  # Crop from the left
                y1=0,#crop_height, # Crop from the top
                x2=width - crop_width,  # Crop from the right
                y2=height - crop_height # Crop from the bottom
            )
        ])
        # Check if the video is landscape (width > height)
        print("Converting landscape video to Reels format...")

        if clip.size[0] > clip.size[1]:
            print("Converting landscape video to Reels format...")
            
            # Resize to 9:16 aspect ratio with zoomed-in background (cropping/zooming part)
            # Make background video (zoomed & blurred)
            width = clip.size[1] * height // clip.size[0]
            background_clip = clip.with_effects([vfx.Resize(height=height),vfx.Crop(x_center=clip.size[0] // 2, width=width)])
            background_clip = background_clip.transform(self.blur)
            # Crop the original clip to center and match the 9:16 aspect ratio
            main_clip = clip.with_effects([vfx.Resize(width=width)]) 
            
            logo = ImageClip("./logo/logo.png").with_duration(clip.duration)
            logo = logo.with_effects([vfx.Resize(height=50),vfx.Margin(bottom=10,opacity=0)])
            
            # Overlay the main clip on the background clip
            final_clip = CompositeVideoClip([background_clip, main_clip.with_position(("center", "center")),logo.with_position(("center", "bottom"))])
            
            # Write the final clip to file with a more comprehensive argument for video settings
            final_clip.write_videofile(output_clip, codec="libx264", audio_codec="aac", threads=12, preset='ultrafast',ffmpeg_params=["-movflags", "+faststart"])
            print(f"Reels format video saved to {output_clip}")
        else:
            print("No need for conversion, video is already in vertical format.")

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

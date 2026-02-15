from curl_cffi import  requests
import re
import json
import os
import subprocess
from yt_dlp import YoutubeDL
from typing import List
import os
import subprocess
from typing import List
from moviepy import VideoFileClip, CompositeVideoClip, ImageClip
import moviepy.video.fx as vfx
import os
from skimage.filters import gaussian


def getPointsList(url,x:int)->list:
    response = requests.get(url)
    if "macroMarkersListEntity" in response.text:
        print("found")

    # Match the JSON part within the HTML script tag
    script_match = re.search(r'<script.*?>\s*var ytInitialData = ({.*?});\s*</script>', response.text, re.DOTALL)

    # Extract and parse the JSON if found
    if script_match:
        json_str = script_match.group(1)
        try:
            json_data = json.loads(json_str)
            json_data  # Parsed JSON object
        except json.JSONDecodeError as e:
            f"Error parsing JSON: {e}"
    else:
        "Script containing 'ytInitialData' not found."

    scores = json_data["frameworkUpdates"]["entityBatchUpdate"]["mutations"][0]["payload"]["macroMarkersListEntity"]["markersList"]["markers"]
    max_intensity_elements = sorted(scores, key=lambda x: x['intensityScoreNormalized'])
    return max_intensity_elements[-x:]

def getPointsListConcater(url: str, x: int) -> list:
    response = requests.get(url)
    if "macroMarkersListEntity" in response.text:
        print("found")

    # Match the JSON part within the HTML script tag
    script_match = re.search(r'<script.*?>\s*var ytInitialData = ({.*?});\s*</script>', response.text, re.DOTALL)

    # Extract and parse the JSON if found
    if script_match:
        json_str = script_match.group(1)
        try:
            json_data = json.loads(json_str)
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON: {e}")
            return []
    else:
        print("Script containing 'ytInitialData' not found.")
        return []

    # Extract the scores
    print(json_data)
    scores = json_data["frameworkUpdates"]["entityBatchUpdate"]["mutations"][0]["payload"]["macroMarkersListEntity"]["markersList"]["markers"]
    
    for score in scores:
        score['durationMillis'] = int(score['durationMillis'])
    # Merge consecutive clips if their intensityScoreNormalized is over 0.5
    i = 0
    while i < len(scores) - 1:
        current = scores[i]
        next_point = scores[i + 1]

        # If both have intensity score over 0.5, merge them
        if current['intensityScoreNormalized'] > 0.5 and next_point['intensityScoreNormalized'] > 0.5:
            # Merge the next point into the current point
            print("Concat")
            current['durationMillis'] += int(next_point['durationMillis'])
            current["intensityScoreNormalized"] = max(current["intensityScoreNormalized"], next_point["intensityScoreNormalized"])
            scores.pop(i + 1)  # Remove the next point
        else:
            i += 1  # Only move to the next point if no merge happens

    # Sort the remaining points by intensity score

    max_intensity_elements = sorted(scores, key=lambda x: x['intensityScoreNormalized'])
    return max_intensity_elements[-x:]

def build_clips(url: str, points: List[dict], output_dir: str = "clips",duration:int = 20) -> None:
    """
    Build video clips from the most-viewed moments of a YouTube video using yt-dlp as a Python library.
    
    Args:
        url (str): The YouTube video URL.
        points (List[dict]): List of moments with their start times and durations.
                             Example: [{'startMillis': '1680360', 'durationMillis': '22110', ...}, ...]
        output_dir (str): Directory to save the clips.
    """
    # Ensure the output directory exists
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    video_file = "temp/video.mp4"
    # Download the video using yt-dlp Python library
    # ydl_opts = {
    #     'format': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
    #     'outtmpl': video_file,
    #     'merge_output_format': 'mp4',  # Ensure video and audio are combined into MP4
    # }
    
    # with YoutubeDL(ydl_opts) as ydl:
    #     ydl.download([url])
    
    # Build clips from the provided points
    for idx, point in enumerate(points):
        start_time = int(point['startMillis']) / 1000  # Convert from milliseconds to seconds 
        output_file = os.path.join(output_dir, f"clip_{idx + 1}.mp4")
        duration = max(int(point['durationMillis']) / 1000, duration)
        # Use ffmpeg to create the clip
        command = [
            "ffmpeg",
            "-ss", str(start_time),  # Seek to 680.6 seconds
            "-i", str(video_file),  # Input file
            "-t", str(duration),  # Duration 20 seconds
            "-c:v", "libx264",  # Video codec
            "-c:a", "aac",  # Audio codec
            "-y",  # Overwrite without asking
            str(output_file)  # Output file
        ]
        subprocess.run(command, shell=True,check=True)
        print(f"Clip {idx + 1} saved to {output_file}")
    
    # Cleanup downloaded video file
    os.remove(video_file)
    print("All clips have been created and saved.")

def blur(gf,t):
    """ Returns a blurred (radius=2 pixels) version of the image """
    image = gf(t)
    return gaussian(image.astype(float), sigma=2)

def convert_to_reels(input_clip: str, output_clip: str,height:int=480) -> None:
    """
    Convert a landscape video clip to a vertical 9:16 Reels format with a zoomed background effect.
    
    Args:
        input_clip (str): Path to the input video clip (landscape format).
        output_clip (str): Path to save the converted Reels clip (vertical format).
    """
    # Load the video clip using MoviePy
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
        background_clip = background_clip.transform(blur)
        # Crop the original clip to center and match the 9:16 aspect ratio
        main_clip = clip.with_effects([vfx.Resize(width=width)]) 
        
        logo = ImageClip("./logo/logo.png").with_duration(clip.duration)
        logo = logo.with_effects([vfx.Resize(height=50),vfx.Margin(bottom=10,opacity=0)])
        
        # Overlay the main clip on the background clip
        final_clip = CompositeVideoClip([background_clip, main_clip.with_position(("center", "center")),logo.with_position(("center", "bottom"))])
        
        # Write the final clip to file with a more comprehensive argument for video settings
        final_clip.write_videofile(output_clip, codec="libx264", audio_codec="aac", threads=4, preset='ultrafast')
        print(f"Reels format video saved to {output_clip}")
    else:
        print("No need for conversion, video is already in vertical format.")

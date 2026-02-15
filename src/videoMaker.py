
import traceback
from src.lib import *


# List of video URLs
file_path = "video_urls.txt"

# Read URLs from the file
with open(file_path, "r", encoding="utf-8") as file:
    video_urls = [line.strip() for line in file if line.strip()]  # Remove empty lines


# Loop through URLs
for url in video_urls:
	# Get video ID from URL
	try:
		points_list = getPointsListConcater(url, 10)  # Get top 5 points
	except Exception as e:
		continue
	
	video_id = url.split("v=")[-1]
	# Ensure the output directory exists
	# Download the video using yt-dlp Python library
	video_file = "temp/video.mp4"
	ydl_opts = {
		'format': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
		'outtmpl': video_file,
		'merge_output_format': 'mp4',  # Ensure video and audio are combined into MP4
	}

	with YoutubeDL(ydl_opts) as ydl:
		ydl.download([url])
	# Create a directory for each video
	video_output_dir = os.path.join("videos", video_id)
	os.makedirs(video_output_dir, exist_ok=True)
	inputVideosDir = os.path.join(video_output_dir, "input")
	os.makedirs(inputVideosDir, exist_ok=True)
	# Get the points list
	video_output_dir = os.path.join(video_output_dir, "output")
	os.makedirs(video_output_dir, exist_ok=True)

	
	# Build the clips
	for i in range(3):
		try:
			build_clips(url, points_list, output_dir=inputVideosDir)
			
			# Process all files in the clips directory and convert them to Reels format
			for file_name in os.listdir(inputVideosDir):
				if file_name.endswith(('.mp4', '.avi', '.mov', '.mkv')):  # Check for valid video formats
					input_clip = os.path.join(inputVideosDir, file_name)
					output_clip = os.path.join(video_output_dir, "reels_" + file_name)
					convert_to_reels(input_clip, output_clip)
			break
		except Exception as e:
			traceback.print_exc()
			continue
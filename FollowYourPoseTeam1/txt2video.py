import argparse
import datetime
import logging
import inspect
import math
import os
from typing import Dict, Optional, Tuple
from omegaconf import OmegaConf

import torch
import torch.nn.functional as F
import torch.utils.checkpoint

import diffusers
import transformers
from accelerate import Accelerator
from accelerate.logging import get_logger
from accelerate.utils import set_seed
from diffusers import AutoencoderKL, DDPMScheduler, DDIMScheduler
from diffusers.optimization import get_scheduler
from diffusers.utils import check_min_version
from diffusers.utils.import_utils import is_xformers_available
from tqdm.auto import tqdm
from transformers import CLIPTextModel, CLIPTokenizer

from followyourpose.models.unet import UNet3DConditionModel
from followyourpose.data.hdvila import HDVilaDataset
from followyourpose.pipelines.pipeline_followyourpose import FollowYourPosePipeline
from followyourpose.util import save_videos_grid, ddim_inversion
from einops import rearrange
from moviepy.editor import VideoFileClip

import sys
sys.path.append('FollowYourPose')

# Will error if the minimal version of diffusers is not installed. Remove at your own risks.
check_min_version("0.10.0.dev0")

logger = get_logger(__name__, log_level="INFO")


def main(
    pretrained_model_path: str,
    output_dir: str,
    validation_data: Dict,
    validation_steps: int = 100,
    train_batch_size: int = 1,
    gradient_accumulation_steps: int = 1,
    gradient_checkpointing: bool = True,
    resume_from_checkpoint: Optional[str] = None,
    mixed_precision: Optional[str] = "fp16",
    enable_xformers_memory_efficient_attention: bool = True,
    seed: Optional[int] = None,
    skeleton_path: Optional[str] = None,
):
    *_, config = inspect.getargvalues(inspect.currentframe())

    accelerator = Accelerator(
        gradient_accumulation_steps=gradient_accumulation_steps,
        mixed_precision=mixed_precision,
    )

    # Make one log on every process with the configuration for debugging.
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%m/%d/%Y %H:%M:%S",
        level=logging.INFO,
    )
    logger.info(accelerator.state, main_process_only=False)
    if accelerator.is_local_main_process:
        transformers.utils.logging.set_verbosity_warning()
        diffusers.utils.logging.set_verbosity_info()
    else:
        transformers.utils.logging.set_verbosity_error()
        diffusers.utils.logging.set_verbosity_error()

    # If passed along, set the training seed now.
    if seed is not None:
        set_seed(seed)

    # Handle the output folder creation
    if accelerator.is_main_process:
        # now = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        # output_dir = os.path.join(output_dir, now)
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(f"{output_dir}/samples", exist_ok=True)
        os.makedirs(f"{output_dir}/inv_latents", exist_ok=True)
        OmegaConf.save(config, os.path.join(output_dir, 'config.yaml'))

    # Load scheduler, tokenizer and models.
    noise_scheduler = DDPMScheduler.from_pretrained(pretrained_model_path, subfolder="scheduler")
    tokenizer = CLIPTokenizer.from_pretrained(pretrained_model_path, subfolder="tokenizer")
    text_encoder = CLIPTextModel.from_pretrained(pretrained_model_path, subfolder="text_encoder")
    vae = AutoencoderKL.from_pretrained(pretrained_model_path, subfolder="vae")
    unet = UNet3DConditionModel.from_pretrained_2d(pretrained_model_path, subfolder="unet")

    # Freeze vae and text_encoder
    vae.requires_grad_(False)
    text_encoder.requires_grad_(False)

    unet.requires_grad_(False)

    if enable_xformers_memory_efficient_attention:
        if is_xformers_available():
            unet.enable_xformers_memory_efficient_attention()
        else:
            raise ValueError("xformers is not available. Make sure it is installed correctly")

    if gradient_checkpointing:
        unet.enable_gradient_checkpointing()


    # Get the validation pipeline
    validation_pipeline = FollowYourPosePipeline(
        vae=vae, text_encoder=text_encoder, tokenizer=tokenizer, unet=unet,
        scheduler=DDIMScheduler.from_pretrained(pretrained_model_path, subfolder="scheduler")
    )
    validation_pipeline.enable_vae_slicing()
    ddim_inv_scheduler = DDIMScheduler.from_pretrained(pretrained_model_path, subfolder='scheduler')
    ddim_inv_scheduler.set_timesteps(validation_data.num_inv_steps)

    unet = accelerator.prepare(unet)
    # For mixed precision training we cast the text_encoder and vae weights to half-precision
    # as these models are only used for inference, keeping weights in full precision is not required.
    weight_dtype = torch.float32
    if accelerator.mixed_precision == "fp16":
        weight_dtype = torch.float16
    elif accelerator.mixed_precision == "bf16":
        weight_dtype = torch.bfloat16

    # Move text_encode and vae to gpu and cast to weight_dtype
    text_encoder.to(accelerator.device, dtype=weight_dtype)
    vae.to(accelerator.device, dtype=weight_dtype)

    # We need to recalculate our total training steps as the size of the training dataloader may have changed.

    # We need to initialize the trackers we use, and also store our configuration.
    # The trackers initializes automatically on the main process.
    if accelerator.is_main_process:
        accelerator.init_trackers("text2video-fine-tune")

    global_step = 0
    first_epoch = 0

    # Potentially load in the weights and states from a previous save
    load_path = None
    if resume_from_checkpoint:
        if resume_from_checkpoint != "latest":
            load_path = resume_from_checkpoint
            output_dir = os.path.abspath(os.path.join(resume_from_checkpoint, ".."))
        accelerator.print(f"load from checkpoint {load_path}")
        accelerator.load_state(load_path)

        global_step = int(load_path.split("-")[-1])

                
    if accelerator.is_main_process:
        samples = []
        generator = torch.Generator(device=accelerator.device)
        generator.manual_seed(seed)

        ddim_inv_latent = None

        from datetime import datetime
        import cv2
        now = str(datetime.now())
        
        destination = "/content/GeneratedVideos"

         # Use os.path.basename to get the filename from the path
        filename = os.path.basename(args.config)
        # Use os.path.splitext to split the filename into name and extension
        configName, extension = os.path.splitext(filename)
        
        for idx, prompt in enumerate(validation_data.prompts):
            sample = validation_pipeline(prompt, generator=generator, latents=ddim_inv_latent,
                                        skeleton_path=skeleton_path,
                                        **validation_data).videos
            save_videos_grid(sample, f"{destination}/{configName}-{now}/{prompt}.mp4")

            # # Specify the paths for input and output videos
            input_video_path = f"{destination}/{configName}-{now}/{prompt}.mp4"
            output_video_path = f"{destination}/{configName}-{now}/{prompt}_with_captions.mp4"

            # Open the input video file using OpenCV
            cap = cv2.VideoCapture(input_video_path)
            if not cap.isOpened():
                print(f'Failed to open the video file: {input_video_path}')
            else:
                print(f'Video loaded successfully: {input_video_path}')

            # Get video properties (width, height, and frames per second)
            frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = int(cap.get(cv2.CAP_PROP_FPS))

            # Create a VideoWriter object for the output video with captions
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            out = cv2.VideoWriter(output_video_path, fourcc, fps, (frame_width, frame_height))

            # Define the font and text settings for the caption
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 1
            font_color = (0, 0, 0)
            font_thickness = 2

            # Process and add captions to each frame of the video
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                text_size = cv2.getTextSize(prompt, font, font_scale, font_thickness)[0]
                # Calculate the position of the caption (bottom center)
                text_x = (frame_width - text_size[0]) // 2
                text_y = 30  # Adjust the Y-coordinate for positioning

                # Add the caption to the frame
                cv2.putText(frame, prompt, (text_x, text_y), font, font_scale, font_color, font_thickness)

                # Write the frame with caption to the output video
                out.write(frame)

            # Release resources
            cap.release()
            out.release()

            convert_mp4v_to_h264(output_video_path, output_video_path)

#convert mp4v to h264
def convert_mp4v_to_h264(input_video_path, output_video_path):
    """
    Convert an mp4v video to H.264 format for compatibility with Colab.

    Args:
        input_video_path (str): Path to the input mp4v video file.
        output_video_path (str): Path to save the output video in H.264 format.
    """
    # Load the input video
    video_clip = VideoFileClip(input_video_path)

    os.remove(input_video_path)

    # Convert the video codec to H.264 and save it to the output file
    video_clip.write_videofile(output_video_path, codec="libx264")



if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str)
    parser.add_argument("--skeleton_path", type=str)
    args = parser.parse_args()
    main(**OmegaConf.load(args.config), skeleton_path = args.skeleton_path)

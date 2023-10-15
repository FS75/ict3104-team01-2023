# ict3104-nvidia-project

## Setting up python notebook
- Go to google colab notebook.
- Under Github tab, check "Includ private repos"
- key in the repository link. Eg: "https://github.com/FS75/ict3104-team01-2023" and select the "Main" Branch
- Choose `project.ipynb` and open it in new tab
- Run each cell accordingly and follow the instructions on the notebook


##Note
Original video: Videos downloaded from Charades project
Skeleton: The output created by the cells to be used in the output video
Output Video: The final results based on user prompts, selected original videos

## Data Exploration section
### This contains the lists of videos imported from the Charades project to be used for the model training 
- Run the cell under Data Exploration to watch the lists of videos to be used for training

## Training Section
### This section covers the steps to create the model used by the inference section to create the output video
- Initailize the config / model by inputting the details of configurations
- Run the cells until "Choose a video and click Generate to start training process/generate skeleton."
- Ensure that your runtime restarts by pressing (ctrl  M) before excuting the cell
- Select the video from the dropdown list and generate the skeleton video

## Inference Section
### This section will perform inference and convert prompts keyed in by user into the desired output by combining the skeleton video with the prompts

- Select the model / config file to be used for the inference from the drop down lists. Obtained after running the training section.
- Select the original video to be used to generate the output video
- Key in text prompts that describes what the desired output of the video to be. Eg:"Iron man on the beach"
- Execute the cell to generate the output video based on the prompt and the selected skeleton video.
- Locate the output videos under "/content/ict3104-team01-2023/FollowYourPoseTeam1/checkpoints/inference"


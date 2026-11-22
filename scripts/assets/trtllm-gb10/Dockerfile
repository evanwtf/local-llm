# #383: TensorRT-LLM 1.4.0rc4 plus one patch so GLM-5.3-Flash serves on GB10
# (sm_121). See patch_glm5_next_topk.py for the reason. Build on each node:
#   docker build -t local/tensorrt-llm:1.4.0rc4-gb10topk scripts/assets/trtllm-gb10
FROM nvcr.io/nvidia/tensorrt-llm/release:1.4.0rc4@sha256:813f751bc2c7ed867a53915dedde1c153ca94758e931d989e9c25835f9ba1814
COPY patch_glm5_next_topk.py /tmp/patch_glm5_next_topk.py
RUN python3 /tmp/patch_glm5_next_topk.py && rm /tmp/patch_glm5_next_topk.py

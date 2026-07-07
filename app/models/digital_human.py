from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class DigitalHumanTTSConfig(BaseModel):
    mode: str = "api"
    base_url: str = "http://localhost:7861/"
    generate_path: str = ""
    root_dir: str = ""
    python_path: str = ""
    script_path: str = ""
    config_path: str = ""
    model_dir: str = ""
    default_voice: str = ""

class DigitalHumanHeyGemConfig(BaseModel):
    base_url: str = "http://127.0.0.1:7860/"
    api_base_url: str = "http://127.0.0.1:8383/"
    submit_path: str = "/easy/submit"
    query_path: str = "/easy/query"
    min_resolution: float = 720
    if_res: bool = False
    max_wait_seconds: int = 1800
    stall_timeout_seconds: int = 240

class DigitalHumanRuntimeConfig(BaseModel):
    auto_release_gpu: bool = True
    idle_release_seconds: int = 180
    release_services: List[str] = Field(default_factory=lambda: ["tts", "heygem"])

class DigitalHumanConfigPayload(BaseModel):
    public_base_url: str = ""
    tts: DigitalHumanTTSConfig = DigitalHumanTTSConfig()
    heygem: DigitalHumanHeyGemConfig = DigitalHumanHeyGemConfig()
    runtime: DigitalHumanRuntimeConfig = DigitalHumanRuntimeConfig()

class DigitalHumanPersonPayload(BaseModel):
    id: str = ""
    name: str = ""
    note: str = ""
    default_voice_name: str = ""
    current_video_id: str = ""

class DigitalHumanPersonVideoPayload(BaseModel):
    video: Dict[str, Any] = {}
    name: str = ""
    set_current: bool = True

class DigitalHumanPersonVideosPayload(BaseModel):
    videos: List[Dict[str, Any]] = []
    set_current: bool = True

class DigitalHumanVoiceMetaPayload(BaseModel):
    display_name: str = ""
    note: str = ""

class DigitalHumanTTSOptions(BaseModel):
    speed: float = 1.0
    emo_control_method: str = "与音色参考音频相同"
    emo_ref_url: str = ""
    emo_ref_path: str = ""
    emo_weight: float = 0.8
    emo_text: str = ""
    emo_random: bool = False
    max_tokens: float = 120
    vec1: float = 0
    vec2: float = 0
    vec3: float = 0
    vec4: float = 0
    vec5: float = 0
    vec6: float = 0
    vec7: float = 0
    vec8: float = 0
    do_sample: bool = True
    top_p: float = 0.8
    top_k: float = 30
    temperature: float = 0.8
    length_penalty: float = 0
    num_beams: float = 3
    repetition_penalty: float = 10
    max_mel: float = 1500

class DigitalHumanTTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice_url: str = ""
    voice_path: str = ""
    voice_name: str = ""
    output_name: str = ""
    tts_options: DigitalHumanTTSOptions = Field(default_factory=DigitalHumanTTSOptions)
    config: Optional[DigitalHumanConfigPayload] = None

class DigitalHumanGenerateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice_url: str = ""
    voice_path: str = ""
    voice_name: str = ""
    video_url: str = ""
    video_path: str = ""
    audio_url: str = ""
    code: str = ""
    tts_options: DigitalHumanTTSOptions = Field(default_factory=DigitalHumanTTSOptions)
    config: Optional[DigitalHumanConfigPayload] = None

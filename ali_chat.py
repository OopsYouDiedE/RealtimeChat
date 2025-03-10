import base64
import os
import dashscope
from openai import OpenAI
import dotenv
import pyaudio
import numpy as np
import time
from datetime import datetime

dotenv.load_dotenv()
client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
def get_timestamp():
    now = datetime.now()
    formatted_timestamp = now.strftime("[%Y-%m-%d %H:%M:%S.%f]")
    return formatted_timestamp
def encode_audio(audio_data):
    return base64.b64encode(audio_data).decode("utf-8")

def record_audio(rate=16000, chunk=1024):
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16,
                    channels=1,
                    rate=rate,
                    input=True,
                    frames_per_buffer=chunk)

    print("Listening... (Speak to activate)")
    frames = []
    is_recording = False
    silence_start = None
    recording_start = None
    
    # Add buffer for noise threshold calculation
    noise_buffer = []
    calibration_time = 1  # 1 second for noise calibration
    
    # Calibrate noise level
    print("Calibrating noise level...")
    for _ in range(int(rate * calibration_time / chunk)):
        data = stream.read(chunk)
        noise_buffer.extend(np.frombuffer(data, dtype=np.int16))
    
    noise_threshold = np.abs(noise_buffer).mean() * 2  # Set threshold to 2x ambient noise

    while True:
        data = stream.read(chunk, exception_on_overflow=False)
        audio_data = np.frombuffer(data, dtype=np.int16)
        volume = np.abs(audio_data).mean()

        if not is_recording and volume > noise_threshold:
            print("Voice detected, recording started.")
            is_recording = True
            frames = [data]
            recording_start = time.time()
            silence_start = None
        elif is_recording:
            frames.append(data)
            if volume < noise_threshold:
                if silence_start is None:
                    silence_start = time.time()
                elif time.time() - silence_start >= 1.5:  # Reduced silence threshold to 1.5 seconds
                    break
            else:
                silence_start = None

        if is_recording and time.time() - recording_start >= 10:  # Increased max recording time to 10 seconds
            break

    print("Recording finished.")
    stream.stop_stream()
    stream.close()
    p.terminate()

    audio_data = b''.join(frames)
    return audio_data, len(frames) > 0  # Modified validity check

class Callback(dashscope.audio.tts_v2.ResultCallback):
    _player = None
    _stream = None

    def on_open(self):
        print("Websocket is open.")
        self._player = pyaudio.PyAudio()
        self._stream = self._player.open(
            format=pyaudio.paInt16, 
            channels=1, 
            rate=22050, 
            output=True,
            frames_per_buffer=1024  # Added explicit buffer size
        )

    def on_complete(self):
        print(get_timestamp() + " Speech synthesis task completed successfully.")
        if self._stream and self._stream.is_active():
            self._stream.stop_stream()
            self._stream.close()
        if self._player:
            self._player.terminate()
        self._stream = None
        self._player = None

    def on_error(self, message: str):
        print(f"Speech synthesis task failed: {message}")
        self.on_complete()  # Clean up resources on error

    def on_close(self):
        print(get_timestamp() + " Websocket is closed.")
        self.on_complete()  # Reuse cleanup code

    def on_event(self, message):
        return
        print(f"Event received: {message}")

    def on_data(self, data: bytes) -> None:
        try:
            if self._stream and self._stream.is_active():
                self._stream.write(data)
        except Exception as e:
            print(f"Error playing audio: {e}")
            self.on_complete()

callback = Callback()

messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant. Please reply to the audio content."
                },
                
            ]
while True:
    synthesizer = dashscope.audio.tts_v2.SpeechSynthesizer(
    model="cosyvoice-v1",
    voice="longxiaochun",
    format=dashscope.audio.tts_v2.AudioFormat.PCM_22050HZ_MONO_16BIT,
    callback=callback,
)
    try:
        audio_data, is_valid = record_audio()

        if not is_valid:
            print("Audio clip too short or no voice detected. Continuing to listen...")
            continue

        base64_audio = encode_audio(audio_data)
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": f"data:;base64,{base64_audio}",
                            "format": "wav",
                        },
                    }
                ],
            },
        )
        completion = client.chat.completions.create(
            model="qwen-omni-turbo",
            messages=messages,
            modalities=["text"],
            stream=True,
            stream_options={"include_usage": True},
        )
        content=""
        for chunk in completion:
            if chunk.choices and chunk.choices[0].delta.content:
                d_content = chunk.choices[0].delta.content
                print(d_content,end="")
                content+=d_content
                synthesizer.streaming_call(d_content)
                time.sleep(0.05)  # Reduced delay between chunks
            elif hasattr(chunk, 'usage'):
                print(chunk.usage)
        
        synthesizer.streaming_complete()

        print('[Metric] requestId: {}, first package delay ms: {}'.format(
            synthesizer.get_last_request_id(),
            synthesizer.get_first_package_delay()))
        messages.append({
                        "role": "assistant",
                        "content":content,
                    }
            )
        time.sleep(0.5)  # Reduced pause before next interaction
        
    except Exception as e:
        print(f"Error occurred: {e}")
        time.sleep(1)  # Wait before retrying
        continue


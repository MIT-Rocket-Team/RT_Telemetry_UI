"""Bounded optional transport-video recording, independent of any desktop UI."""
from pathlib import Path
import queue
import subprocess
import threading
from .media import ffmpeg, popen_options

class MissionRecording:
    """Optional bounded host-clock recording; logical timing remains in the sidecar."""
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True)
        self.frames = queue.Queue(maxsize=12)
        self.stopped = threading.Event()
        self.error = ''
        self.process = None
        self.thread = threading.Thread(target=self.run, name='mission-video-recording', daemon=True)
        self.thread.start()

    def feed(self, pixels):
        if self.error:
            raise ValueError(self.error)
        try:
            self.frames.put_nowait(pixels)
        except queue.Full:
            self.error = 'Encoded mission recording cannot keep up; committed frames are preserved'
            self.stopped.set()
            raise ValueError(self.error) from None

    def run(self):
        try:
            with (self.folder / 'encoder.log').open('wb') as log:
                self.process = subprocess.Popen([
                    ffmpeg(), '-hide_banner', '-loglevel', 'error', '-y', '-f', 'rawvideo',
                    '-pixel_format', 'rgb24', '-video_size', '960x540', '-framerate', '30',
                    '-use_wallclock_as_timestamps', '1', '-i', 'pipe:0', '-an', '-c:v', 'mpeg4',
                    '-q:v', '4', '-g', '30', '-pix_fmt', 'yuv420p', '-fps_mode', 'vfr',
                    '-f', 'segment', '-segment_time', '30', '-reset_timestamps', '1',
                    '-segment_list', str(self.folder / 'segments.csv'), '-segment_list_type', 'csv',
                    str(self.folder / 'segment_%05d.mkv')], stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=log, bufsize=0, **popen_options())
                while not self.stopped.is_set() or not self.frames.empty():
                    try:
                        data = memoryview(self.frames.get(timeout=.2))
                    except queue.Empty:
                        if self.process.poll() is not None:
                            raise OSError('Mission video encoder exited early')
                        continue
                    while data:
                        count = self.process.stdin.write(data)
                        if not count:
                            raise OSError('Mission video encoder closed its input')
                        data = data[count:]
                self.process.stdin.close()
                if self.process.wait(timeout=5):
                    raise OSError('Mission video encoder failed; see encoder.log')
        except Exception as exc:  # noqa: BLE001 -- propagate thread failures at the next boundary
            self.error = str(exc)
        finally:
            if self.process and self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=3)

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=7)
        if self.thread.is_alive():
            if self.process:
                self.process.kill()
            self.thread.join(timeout=3)
            self.error = 'Mission recording did not finalize in time'
        if self.error:
            raise ValueError(self.error)

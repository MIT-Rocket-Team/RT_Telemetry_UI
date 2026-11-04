"""Create portable OBS profiles/scene collection, with no credentials or device IDs.

An optional local install adds named profiles without altering existing ones.
OBS must be closed before installation. The fixture collection uses only an
explicit local test movie; it does not request screen or microphone permission.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]


def source(name,kind,settings):
    return dict(name=name,uuid=str(uuid.uuid4()),id=kind,versioned_id=kind,settings=settings,
                mixers=1,sync=0,flags=0,volume=1.0,balance=.5,enabled=True,muted=False,monitoring_type=0)


def collection(name,inputs,scenes):
    sources = list(inputs)
    for title,items in scenes:
        entries = [dict(name=item['name'],source_uuid=item['uuid'],id=i+1,visible=True,locked=True,
                        pos=dict(x=0,y=0),scale=dict(x=1,y=1),rot=0,align=5,bounds_type=2,
                        bounds=dict(x=1920,y=1080),bounds_align=0,crop_left=0,crop_right=0,crop_top=0,crop_bottom=0)
                   for i,item in enumerate(items)]
        sources.append(source(title,'scene',dict(id_counter=len(entries),custom_size=False,items=entries)))
    return dict(name=name,sources=sources,groups=[],scene_order=[dict(name=s) for s,_ in scenes],
                current_scene=scenes[0][0],current_program_scene=scenes[0][0],
                current_transition='Cut',transition_duration=0,transitions=[],quick_transitions=[],
                saved_projectors=[],preview_locked=True,version=2)


def profile(name,width,height,bitrate,recordings):
    return f'''[General]
Name={name}
[Output]
Mode=Advanced
FilenameFormatting=Rocket-%CCYY-%MM-%DD_%hh-%mm-%ss
Reconnect=true
RetryDelay=2
MaxRetries=25
[AdvOut]
Encoder=obs_x264
ApplyServiceSettings=true
TrackIndex=1
RecType=Standard
RecEncoder=none
RecFilePath={recordings}
RecFormat2=mkv
RecTracks=1
Track1Bitrate=128
[Video]
BaseCX=1920
BaseCY=1080
OutputCX={width}
OutputCY={height}
FPSType=0
FPSCommon=30
ScaleType=bicubic
ColorFormat=NV12
ColorSpace=709
ColorRange=Partial
[Audio]
SampleRate=48000
ChannelSetup=Stereo
'''


def prepare(destination,assets,recordings,fixture=None):
    destination.mkdir(parents=True,exist_ok=True)
    recordings.mkdir(parents=True,exist_ok=True)
    for name,width,height,bitrate in [('Rocket 1080p30',1920,1080,14000),('Rocket 720p30',1280,720,5000)]:
        directory = destination/'profiles'/name.replace(' ','_')
        directory.mkdir(parents=True,exist_ok=True)
        (directory/'basic.ini').write_text(profile(name,width,height,bitrate,recordings))
        (directory/'streamEncoder.json').write_text(json.dumps(dict(rate_control='CBR',bitrate=bitrate,keyint_sec=2,
                                                                   preset='veryfast',profile='high'),indent=2))
        # No service.json: OBS is intentionally left without an account or key.
    scene_dir = destination/'scenes'
    scene_dir.mkdir(exist_ok=True)
    slate = source('Rocket emergency slate','image_source',dict(file=str(assets/'standby.png')))
    capture = source('Map Rocket Broadcast — Program window','screen_capture',
                     dict(type=1,window=0,show_cursor=False,show_hidden_windows=False))
    production = collection('Rocket Broadcast',[slate,capture],
                            [('Emergency slate',[slate]),('Rocket Program',[capture])])
    (scene_dir/'Rocket_Broadcast.json').write_text(json.dumps(production,indent=2))
    if fixture:
        movie = source('LOCAL TEST MOVIE — NOT LIVE','ffmpeg_source',
                       dict(is_local_file=True,local_file=str(fixture.resolve()),looping=True,restart_on_activate=True,
                            clear_on_media_end=False,close_when_inactive=False,hw_decode=True))
        rehearsal = collection('Rocket Local Rehearsal',[movie,slate],
                               [('LOCAL FIXTURE — NOT LIVE',[movie]),('Emergency slate',[slate])])
        (scene_dir/'Rocket_Local_Rehearsal.json').write_text(json.dumps(rehearsal,indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'build/obs-setup')
    parser.add_argument('--fixture',type=Path)
    parser.add_argument('--install',action='store_true')
    args = parser.parse_args()
    recordings = Path.home()/'Movies/Rocket Broadcast'
    prepare(args.output,ROOT/'resources/broadcast',recordings,args.fixture)
    if args.install:
        if sys.platform != 'darwin': raise SystemExit('Automatic profile installation is macOS-only')
        obs = Path.home()/'Library/Application Support/obs-studio/basic'
        for folder in ('profiles','scenes'):
            for entry in (args.output/folder).iterdir():
                target = obs/folder/entry.name
                if target.exists(): raise SystemExit(f'Refusing to overwrite existing OBS preset: {target}')
                target.parent.mkdir(parents=True,exist_ok=True)
                if entry.is_dir(): shutil.copytree(entry,target)
                else: shutil.copy2(entry,target)
    print(args.output)


if __name__ == '__main__': main()

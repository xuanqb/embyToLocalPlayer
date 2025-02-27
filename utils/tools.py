import json
import os.path
import re
import signal
import subprocess
import threading
import time
import urllib.parse
from typing import Union

from utils.configs import configs, MyLogger

_logger = MyLogger()


def logger_setup(data):
    if not configs.raw.getboolean('dev', 'mix_log', fallback=True):
        MyLogger.need_mix = False
        return
    MyLogger.api_key = data['api_key']
    MyLogger.netloc = data['netloc']
    MyLogger.netloc_replace = MyLogger.mix_host_gen(data['netloc'])


def safe_deleter(file, ext: Union[str, list, tuple] = ('mkv', 'mp4', 'srt', 'ass')):
    ext = [ext] if isinstance(ext, str) else ext
    *_, f_ext = os.path.splitext(file)
    if f_ext.replace('.', '') in ext and os.path.exists(file):
        os.remove(file)
        return True


def clean_tmp_dir():
    tmp = os.path.join(configs.cwd, '.tmp')
    if os.path.isdir(tmp):
        for file in os.listdir(tmp):
            os.remove(os.path.join(tmp, file))


def scan_cache_dir():
    """:return dict(_id=i.name, path=i.path, stat=i.stat())"""
    return [dict(_id=i.name, path=i.path, stat=i.stat()) for i in os.scandir(configs.cache_path) if i.is_file()]


def load_json_file(file, error_return='list', encoding='utf-8'):
    try:
        with open(file, encoding=encoding) as f:
            _json = json.load(f)
    except (FileNotFoundError, ValueError):
        print(f'load json file fail, fallback to {error_return}')
        return dict(list=[], dict={})[error_return]
    else:
        return _json


def dump_json_file(obj, file, encoding='utf-8'):
    with open(file, 'w', encoding=encoding) as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


class ThreadWithReturnValue(threading.Thread):
    def __init__(self, group=None, target=None, name=None, args=(), kwargs=None, *, daemon=None):
        threading.Thread.__init__(self, group, target, name, args, kwargs, daemon=daemon)
        self._return = None

    def run(self):
        if self._target is not None:
            self._return = self._target(*self._args, **self._kwargs)

    def join(self):
        threading.Thread.join(self)
        return self._return


def open_local_folder(data):
    path = data.get('full_path') or data['info'][0]['content_path']
    translate_path = translate_path_by_ini(path)
    path = os.path.normpath(translate_path)
    # isdir = os.path.isdir(path)
    isdir = False if os.path.splitext(path)[1] else True
    windows = f'explorer "{path}"' if isdir else f'explorer /select, "{path}"'
    # -R 确保前台显示
    darwin = f'open -R "{path}"'
    linux = f'xdg-open "{path}"' if isdir else f'xdg-open "{os.path.dirname(path)}"'
    cmd = dict(windows=windows, darwin=darwin, linux=linux)[configs.platform.lower()]
    _logger.info(cmd)
    os.system(cmd)


def play_media_file(data):
    save_path = data['info'][0]['save_path']
    big_file = sorted(data['file'], key=lambda i: i['size'], reverse=True)[0]['name']
    file_path = os.path.join(save_path, big_file)
    media_path = translate_path_by_ini(file_path)
    cmd = get_player_cmd(media_path, file_path=file_path)
    player = subprocess.Popen(cmd)
    activate_window_by_pid(player.pid)


def kill_multi_process(name_re, not_re=None):
    if os.name == 'nt':
        from utils.windows_tool import list_pid_and_cmd
        pid_cmd = list_pid_and_cmd(name_re)
    else:
        ps_out = subprocess.Popen(['ps', '-eo', 'pid,command'], stdout=subprocess.PIPE,
                                  encoding='utf-8').stdout.readlines()
        pid_cmd = [i.strip().split(maxsplit=1) for i in ps_out[1:]]
        pid_cmd = [(int(pid), cmd) for (pid, cmd) in pid_cmd if re.search(name_re, cmd)]
    pid_cmd = [(int(pid), cmd) for (pid, cmd) in pid_cmd if not re.search(not_re, cmd)] if not_re else pid_cmd
    my_pid = os.getpid()
    for pid, _ in pid_cmd:
        if pid != my_pid:
            _logger.info('kill', pid, _)
            os.kill(pid, signal.SIGABRT)
    time.sleep(1)


def activate_window_by_pid(pid, sleep=0):
    if os.name != 'nt':
        time.sleep(1.5)
        return

    from utils.windows_tool import activate_window_by_win32

    def activate_loop():
        for _ in range(100):
            time.sleep(0.5)
            if activate_window_by_win32(pid):
                return

    threading.Thread(target=activate_loop).start()
    time.sleep(sleep)


def force_disk_mode_by_path(file_path):
    ini_str = configs.raw.get('dev', 'force_disk_mode_path', fallback='').replace('，', ',')
    if not ini_str:
        return False
    ini_tuple = tuple(i.strip() for i in ini_str.split(',') if i)
    check = file_path.startswith(ini_tuple)
    _logger.info('disk_mode check', check)
    return check


def use_dandan_exe_by_path(file_path):
    config = configs.raw
    dandan = config['dandan'] if 'dandan' in config.sections() else {}
    if not dandan or not file_path or not dandan.getboolean('enable'):
        return False
    enable_path = dandan.get('enable_path', '').replace('，', ',')
    enable_path = [i.strip() for i in enable_path.split(',') if i]
    path_match = [path in file_path for path in enable_path]
    if any(path_match) or not enable_path:
        return True
    _logger.error(f'dandanplay {enable_path=} \n{path_match=}')


def translate_path_by_ini(file_path, debug=False):
    config = configs.raw
    path_check = config.getboolean('dev', 'path_check', fallback=False)
    if 'src' in config and 'dst' in config and not file_path.startswith('http'):
        src = config['src']
        dst = config['dst']
        # 貌似是有序字典
        for k, src_prefix in src.items():
            if not file_path.startswith(src_prefix):
                continue
            dst_prefix = dst[k]
            tmp_path = file_path.replace(src_prefix, dst_prefix, 1)
            if not path_check:
                file_path = os.path.abspath(tmp_path)
                break
            elif os.path.exists(tmp_path):
                file_path = os.path.abspath(tmp_path)
                break
            else:
                # path_check = True and debug = True and exists = False
                _log = _logger.info if debug else _logger.debug
                _log('debug: dev > path_check: fail >', tmp_path)
    return file_path


def select_player_by_path(file_path):
    data = configs.raw.get('dev', 'player_by_path', fallback='')
    if not data:
        return False
    data = data.replace('：', ':').replace('，', ',').replace('；', ';')
    data = [i.strip() for i in data.split(';') if i]
    path_map = {}
    for rule in data:
        player, path = [i.strip() for i in rule.split(':', maxsplit=1)]
        for p in [i.strip() for i in path.split(',') if i]:
            path_map[p] = player
    result = [player for path, player in path_map.items() if path in file_path]
    return result[0] if result else False


def get_player_cmd(media_path, file_path):
    config = configs.raw
    player = config['emby']['player']
    exe = config['exe'][player]
    exe = config['dandan']['exe'] if use_dandan_exe_by_path(file_path) else exe
    if player_by_path := select_player_by_path(file_path):
        exe = config['exe'][player_by_path]
    result = [exe, media_path]
    _logger.info('command line:', result)
    if not media_path.startswith('http') and not os.path.exists(media_path):
        raise FileNotFoundError(f'{media_path}\nmay need to disable read disk mode, '
                                f'or enable path_check, see detail in FAQ')
    return result


def version_prefer_emby(sources):
    rules = configs.ini_str_split('dev', 'version_prefer')
    if not rules:
        return sources[0]
    rules = [i.lower() for i in rules]
    name_list = [os.path.basename(i).lower() for i in [s['Path'] for s in sources]]
    join_str = '_|_'
    name_all = join_str.join(name_list)
    for rule in rules:
        if rule not in name_all:
            continue
        name_all = name_all[:name_all.index(rule)]
        name_list = name_all.split(join_str)
        index = len(name_list) - 1
        _logger.info(f'version_prefer: success with {rule=}')
        return sources[index]
    _logger.info(f'version_prefer: fail')
    return sources[0]


def parse_received_data_emby(received_data):
    api_client = received_data['ApiClient']
    mount_disk_mode = True if received_data['mountDiskEnable'] == 'true' else False
    url = urllib.parse.urlparse(received_data['playbackUrl'])
    headers = received_data['request']['headers']
    is_emby = True if '/emby/' in url.path else False
    jellyfin_auth = headers['X-Emby-Authorization'] if not is_emby else ''
    jellyfin_auth = [i.replace('\'', '').replace('"', '').strip().split('=')
                     for i in jellyfin_auth.split(',')] if not is_emby else []
    jellyfin_auth = dict((i[0], i[1]) for i in jellyfin_auth if len(i) == 2)

    query = dict(urllib.parse.parse_qsl(url.query))
    query: dict
    item_id = [str(i) for i in url.path.split('/')]
    item_id = item_id[item_id.index('Items') + 1]
    media_source_id = query.get('MediaSourceId')
    api_key = query['X-Emby-Token'] if is_emby else jellyfin_auth['Token']
    scheme, netloc = api_client['_serverAddress'].split('://')
    device_id = query['X-Emby-Device-Id'] if is_emby else jellyfin_auth['DeviceId']
    sub_index = int(query.get('SubtitleStreamIndex', -1))

    data = received_data['playbackData']
    media_sources = data['MediaSources']
    play_session_id = data['PlaySessionId']
    if media_source_id:
        media_source_info = [i for i in media_sources if i['Id'] == media_source_id][0]
    else:
        media_source_info = version_prefer_emby(media_sources) \
            if len(media_sources) > 1 and is_emby else media_sources[0]
        media_source_id = media_source_info['Id']
    file_path = media_source_info['Path']
    # stream_url = f'{scheme}://{netloc}{media_source_info["DirectStreamUrl"]}'
    container = os.path.splitext(file_path)[-1]
    extra_str = '/emby' if is_emby else ''
    stream_url = f'{scheme}://{netloc}{extra_str}/videos/{item_id}/stream{container}' \
                 f'?DeviceId={device_id}&MediaSourceId={media_source_id}&Static=true' \
                 f'&PlaySessionId={play_session_id}&api_key={api_key}'

    if stream_redirect := configs.ini_str_split('dev', 'stream_redirect'):
        stream_redirect = zip(stream_redirect[0::2], stream_redirect[1::2])
        for (_raw, _jump) in stream_redirect:
            stream_url = stream_url.replace(_raw, _jump)
    # 避免将内置字幕转为外挂字幕，内置字幕选择由播放器决定
    sub_index = sub_index if sub_index < 0 or media_source_info['MediaStreams'][sub_index]['IsExternal'] else -2
    sub_lang = tuple(configs.ini_str_split('dev', 'sub_lang_check'))
    if not mount_disk_mode and sub_lang and sub_index == -1:
        sub_streams = [i for i in media_source_info['MediaStreams'] if i['Type'] == 'Subtitle']
        sub_check = [i for i in sub_streams if not i['IsExternal'] and i.get('Language', '').startswith(sub_lang)]
        sub_ext = [i for i in sub_streams if i['IsExternal']]
        if not sub_check and sub_ext:
            sub_index = sub_ext[0]['Index']
    if not mount_disk_mode and sub_index >= 0:
        sub_jellyfin_str = '' if is_emby \
            else f'{item_id[:8]}-{item_id[8:12]}-{item_id[12:16]}-{item_id[16:20]}-{item_id[20:]}/'
        sub_emby_str = f'/{media_source_id}' if is_emby else ''
        # sub_data = media_source_info['MediaStreams'][sub_index]
        sub_data = [i for i in media_source_info['MediaStreams'] if i['Index'] == sub_index][0]
        fallback_sub = f'{extra_str}/videos/{sub_jellyfin_str}{item_id}{sub_emby_str}/Subtitles' \
                       f'/{sub_index}/0/Stream.{sub_data["Codec"]}?api_key={api_key}'
        sub_delivery_url = sub_data['Codec'] != 'sup' and sub_data.get('DeliveryUrl') or fallback_sub
    else:
        sub_delivery_url = None
    sub_file = f'{scheme}://{netloc}{sub_delivery_url}' if sub_delivery_url else None
    mount_disk_mode = True if force_disk_mode_by_path(file_path) else mount_disk_mode
    media_path = translate_path_by_ini(file_path, debug=True) if mount_disk_mode else stream_url
    basename = os.path.basename(file_path)
    media_basename = os.path.basename(media_path)
    if file_path.endswith('.m3u8'):
        media_path = stream_url = file_path

    media_title = basename if not mount_disk_mode else None  # 播放http时覆盖标题

    seek = query['StartTimeTicks']
    start_sec = int(seek) // (10 ** 7) if seek else 0
    server = 'emby' if is_emby else 'jellyfin'

    fake_name = os.path.splitdrive(file_path)[1].replace('/', '__').replace('\\', '__')
    total_sec = int(media_source_info['RunTimeTicks']) // 10 ** 7 if 'RunTimeTicks' in media_source_info else 10 ** 12
    position = start_sec / total_sec
    user_id = query['UserId']

    result = dict(
        server=server,
        mount_disk_mode=mount_disk_mode,
        api_key=api_key,
        scheme=scheme,
        netloc=netloc,
        media_path=media_path,
        start_sec=start_sec,
        sub_file=sub_file,
        media_title=media_title,
        play_session_id=play_session_id,
        device_id=device_id,
        headers=headers,
        item_id=item_id,
        file_path=file_path,
        stream_url=stream_url,
        fake_name=fake_name,
        position=position,
        total_sec=total_sec,
        user_id=user_id,
        basename=basename,
        media_basename=media_basename,
        fist_time=received_data['fistTime'],
    )
    return result


def parse_received_data_plex(received_data):
    mount_disk_mode = True if received_data['mountDiskEnable'] == 'true' else False
    url = urllib.parse.urlparse(received_data['playbackUrl'])
    query = dict(urllib.parse.parse_qsl(url.query))
    query: dict
    api_key = query['X-Plex-Token']
    client_id = query['X-Plex-Client-Identifier']
    netloc = url.netloc
    scheme = url.scheme
    metas = received_data['playbackData']['MediaContainer']['Metadata']
    _file = metas[0]['Media'][0]['Part'][0]['file']
    mount_disk_mode = True if force_disk_mode_by_path(_file) else mount_disk_mode
    base_info_dict = dict(server='plex',
                          mount_disk_mode=mount_disk_mode,
                          api_key=api_key,
                          scheme=scheme,
                          netloc=netloc,
                          client_id=client_id,
                          )
    res_list = []
    fist_sub = None
    sub_lang = tuple(configs.ini_str_split('dev', 'sub_lang_check'))
    for index, meta in enumerate(metas):
        res = base_info_dict.copy()
        data = meta['Media'][0]
        item_id = data['id']
        duration = data['duration']
        file_path = data['Part'][0]['file']
        size = data['Part'][0]['size']
        stream_path = data['Part'][0]['key']
        stream_url = f'{scheme}://{netloc}{stream_path}?download=1&X-Plex-Token={api_key}'
        sub_streams = [i for i in data['Part'][0]['Stream'] if i.get('streamType') == 3]
        if index == 0:
            fist_sub = [i for i in sub_streams if i.get('selected')]
            if sub_lang and not fist_sub:
                sub_check = [i for i in sub_streams if not i.get('key') and i['languageCode'].startswith(sub_lang)]
                sub_ext = [i for i in sub_streams if i.get('key')]
                if not sub_check and sub_ext:
                    fist_sub = sub_ext
            sub_path = fist_sub
        else:
            sub_path = [s for s in sub_streams if fist_sub and s.get('key')
                        and configs.check_str_match(s['displayTitle'], 'playlist', 'subtitle_priority', log=False)]
        sub_file = f'{scheme}://{netloc}{sub_path[0]["key"]}?download=1&X-Plex-Token={api_key}' \
            if not mount_disk_mode and sub_path and sub_path[0].get('key') else None
        media_path = translate_path_by_ini(file_path) if mount_disk_mode else stream_url
        basename = os.path.basename(file_path)
        media_basename = os.path.basename(media_path)
        media_title = basename if not mount_disk_mode else None  # 播放http时覆盖标题

        seek = meta.get('viewOffset')
        rating_key = meta['ratingKey']
        start_sec = int(seek) // (10 ** 3) if seek and not query.get('extrasPrefixCount') else 0

        fake_name = os.path.splitdrive(file_path)[1].replace('/', '__').replace('\\', '__')
        total_sec = int(meta['duration']) // (10 ** 3)
        position = start_sec / total_sec

        provider_ids = [tuple(i['id'].split('://')) for i in meta['Guid']]
        provider_ids = {k.title(): v for (k, v) in provider_ids}

        trakt_emby_ver_dict = dict(
            Type=meta['type'],
            ProviderIds=provider_ids
        )

        playlist_diff_dict = dict(
            basename=basename,
            media_basename=media_basename,
            item_id=item_id,
            file_path=file_path,
            stream_url=stream_url,
            media_path=media_path,
            fake_name=fake_name,
            total_sec=total_sec,
            sub_file=sub_file,
            index=index,
            size=size
        )

        other_info_dict = dict(
            start_sec=start_sec,
            media_title=media_title,
            duration=duration,
            rating_key=rating_key,
            position=position,
        )
        res.update(trakt_emby_ver_dict)
        res.update(playlist_diff_dict)
        res.update(other_info_dict)
        res_list.append(res)

    result = res_list[0]
    result['list_eps'] = res_list
    return result


def show_confirm_button(message, width, height, result, fallback, timeout=3):
    import tkinter as tk
    res = fallback

    def _main():
        root = tk.Tk()
        root.title('Confirm Button')
        root.attributes('-topmost', True)
        root.bind('<Motion>', lambda i: root.attributes('-topmost', False))
        screenwidth = root.winfo_screenwidth()
        screenheight = root.winfo_screenheight()
        align_str = '%dx%d+%d+%d' % (width, height, (screenwidth - width) / 2, (screenheight - height) / 2)
        root.geometry(align_str)
        root.resizable(width=False, height=False)

        def click():
            nonlocal res
            res = result
            root.destroy()

        tk.Button(root, height=height - 5, width=width - 5, text=message, command=click).pack()
        root.after(timeout * 1000, root.destroy)
        root.mainloop()

    _main()
    return res

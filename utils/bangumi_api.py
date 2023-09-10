import datetime
import difflib
import os
import typing

import requests


class BangumiApi:
    def __init__(self, username=None, access_token=None, private=True, http_proxy=None):
        self.host = 'https://api.bgm.tv/v0'
        self.username = username
        self.private = private
        self.req = requests.Session()
        self._req_not_auth = requests.Session()

        for r in self.req, self._req_not_auth:
            r.headers.update({'Accept': 'application/json', 'User-Agent': 'kjtsune/embyBangumi'})
            if access_token:
                r.headers.update({'Authorization': f'Bearer {access_token}'})
            if http_proxy:
                r.proxies = {'http': http_proxy, 'https': http_proxy}
        self._req_not_auth.headers = {k: v for k, v in self._req_not_auth.headers.items() if k != 'Authorization'}

    def get(self, path, params=None):
        res = self.req.get(f'{self.host}/{path}',
                           params=params)
        return res

    def post(self, path, _json, params=None):
        res = self.req.post(f'{self.host}/{path}',
                            json=_json, params=params)
        return res

    def put(self, path, _json, params=None):
        res = self.req.put(f'{self.host}/{path}',
                           json=_json, params=params)
        return res

    def patch(self, path, _json, params=None):
        res = self.req.patch(f'{self.host}/{path}',
                             json=_json, params=params)
        return res

    def get_me(self):
        res = self.get('me')
        if 400 <= res.status_code < 500:
            if os.name == 'nt':
                os.startfile('https://next.bgm.tv/demo/access-token')
            raise ValueError('BangumiApi: Unauthorized, access_token may wrong')
        return res.json()

    def search(self, title, start_date, end_date, limit=5):
        res = self._req_not_auth.post(f'{self.host}/search/subjects',
                                      json={'keyword': title,
                                            'filter': {'type': [2],
                                                       'air_date': [f'>={start_date}',
                                                                    f'<{end_date}'],
                                                       'nsfw': True}},
                                      params={'limit': limit}, )
        return res.json()

    def search_old(self, title):
        res = self.req.get(f'{self.host[:-2]}/search/subject/{title}', params={'type': 2})
        return res.json()

    def get_subject(self, subject_id):
        res = self.get(f'subjects/{subject_id}')
        return res.json()

    def get_related_subjects(self, subject_id):
        res = self.get(f'subjects/{subject_id}/subjects')
        return res.json()

    def get_episodes(self, subject_id, _type=0):
        res = self.get('episodes', params={
            'subject_id': subject_id,
            'type': _type,
        })
        return res.json()

    def get_target_season_episode_id(self, subject_id, target_season: int, target_ep: typing.Union[int, list] = None):
        season_num = 1
        current_id = subject_id
        ep_num_list = target_ep if isinstance(target_ep, list) else None
        target_ep = ep_num_list[0] if isinstance(target_ep, list) else target_ep

        if target_season >= 5:
            if target_ep:
                return None, None
            return None
        if target_ep and target_ep > 50:
            return None, None

        if target_season == 1:
            fist_part = True
            while True:
                if not target_ep:
                    return current_id
                ep_info = self.get_episodes(current_id)['data']
                _target_ep = [i for i in ep_info if i['sort'] == target_ep]
                if _target_ep:
                    if ep_num_list:
                        return current_id, [i['id'] for i in ep_info if i['sort'] in ep_num_list]
                    return current_id, _target_ep[0]['id']
                is_new_season = True if ep_info[0]['sort'] <= 1 else False
                if not fist_part and is_new_season:
                    break
                related = self.get_related_subjects(current_id)
                next_id = [i for i in related if i['relation'] == '续集']
                if not next_id:
                    break
                current_id = next_id[0]['id']
                fist_part = False
            raise ValueError(f'{subject_id=} {target_season=} {target_ep=} not found')

        while True:
            related = self.get_related_subjects(current_id)
            next_id = [i for i in related if i['relation'] == '续集']
            if not next_id:
                break
            current_id = next_id[0]['id']
            ep_info = self.get_episodes(current_id)['data']
            is_new_season = True if ep_info[0]['sort'] <= 1 else False
            _target_ep = [i for i in ep_info if i['sort'] == target_ep]
            need_next_part = False if target_ep and _target_ep else True
            if is_new_season:
                season_num += 1
            if is_new_season and need_next_part:
                season_num -= 1
            if season_num == target_season:
                if not target_ep:
                    return current_id
                if _target_ep:
                    if ep_num_list:
                        return current_id, [i['id'] for i in ep_info if i['sort'] in ep_num_list]
                    return current_id, _target_ep[0]['id']
                if need_next_part:
                    continue
                break
        raise ValueError(f'{subject_id=} {target_season=} {target_ep=} not found')

    def get_subject_collection(self, subject_id):
        res = self.get(f'users/{self.username}/collections/{subject_id}')
        if res.status_code == 404:
            return {}
        return res.json()

    def mark_episode_watched(self, subject_id, ep_id):
        data = self.get_subject_collection(subject_id)
        if data.get('type') == 2:
            return
        if not data:
            self.add_collection_subject(subject_id=subject_id)
            self.change_episode_state(ep_id=ep_id, state=2)
            return
        self.change_episode_state(ep_id=ep_id, state=2)

    def add_collection_subject(self, subject_id, private=None, state=3):
        private = self.private if private is None else private
        self.post(f'users/-/collections/{subject_id}',
                  _json={'type': state,
                         'private': bool(private)})

    def change_episode_state(self, ep_id, state=2):
        res = self.put(f'users/-/collections/-/episodes/{ep_id}',
                       _json={'type': state})
        if res.status_code > 333:
            raise ValueError(f'{res.status_code=} {res.text}')
        return res


class BangumiApiEmbyVer(BangumiApi):
    @staticmethod
    def _emby_filter(bgm_data):
        useful_key = ['date', 'id', 'name', 'name_cn', 'rank', 'score', ]
        update_date = str(datetime.date.today())
        if isinstance(bgm_data, list):
            res = []
            for data in bgm_data:
                d = {k: v for k, v in data.items() if k in useful_key}
                d['update_date'] = update_date
                res.append(d)
            return res
        else:
            d = {k: v for k, v in bgm_data.items() if k in useful_key}
            d['update_date'] = update_date
            return d

    def emby_search(self, title, ori_title, premiere_date: str, is_movie=False):
        air_date = datetime.datetime.fromisoformat(premiere_date[:10])
        start_date = air_date - datetime.timedelta(days=2)
        end_date = air_date + datetime.timedelta(days=2)
        bgm_data = None
        if ori_title:
            bgm_data = self.search(title=ori_title, start_date=start_date, end_date=end_date)
        if not bgm_data:
            bgm_data = self.search(title=title, start_date=start_date, end_date=end_date)
            if not bgm_data and is_movie:
                title = ori_title or title
                end_date = air_date + datetime.timedelta(days=200)
                bgm_data = self.search(title=title, start_date=start_date, end_date=end_date)
        if not bgm_data:
            return
        return self._emby_filter(bgm_data['data'])

    @staticmethod
    def title_diff_ratio(title, ori_title, bgm_data):
        ori_title = ori_title or title
        ratio = max(difflib.SequenceMatcher(None, bgm_data['name'], ori_title).quick_ratio(),
                    difflib.SequenceMatcher(None, bgm_data['name_cn'], title).quick_ratio(),
                    difflib.SequenceMatcher(None, bgm_data['name'], title).quick_ratio())
        return ratio

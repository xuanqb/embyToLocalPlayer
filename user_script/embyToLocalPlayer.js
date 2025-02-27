// ==UserScript==
// @name         embyToLocalPlayer
// @name:zh-CN   embyToLocalPlayer
// @name:en      embyToLocalPlayer
// @namespace    https://github.com/kjtsune/embyToLocalPlayer
// @version      1.1.10.1
// @description  需要 Python。Emby/Jellyfin 调用外部本地播放器，并回传播放记录。适配 Plex。
// @description:zh-CN 需要 Python。Emby/Jellyfin 调用外部本地播放器，并回传播放记录。适配 Plex。
// @description:en  Require Python. Play in an external player. Update watch history to Emby/Jellyfin server. Support Plex.
// @author       Kjtsune
// @match        *://*/web/index.html*
// @match        *://*/*/web/index.html*
// @include      /:\/\/[\w.]*\/web\/$/
// @icon         https://www.google.com/s2/favicons?sz=64&domain=emby.media
// @grant        unsafeWindow
// @grant        GM_xmlhttpRequest
// @grant        GM_registerMenuCommand
// @grant        GM_unregisterMenuCommand
// @run-at       document-start
// @connect      127.0.0.1
// @license MIT
// ==/UserScript==
'use strict';
/*
2023-10-04:
1. pot 和 vlc(Linux/macOS) 网络外挂字幕时，使用连播代替播放列表。`.ini` > playlist
2. 高版本 macOS 自启方案。@Eatsolx
* 版本间累积更新：
  * 增加：bangumi.tv bgm.tv 单向同步支持。见 FAQ。
  * Jellyfin: 适配 基础 URL。
  * 可一键更新。见 FAQ。
  * 可保存日志。ini > dev > log_file。
  * Trakt：未启用播放列表时同步失效。
  * Trakt：自启时误弹认证窗口。
  * 播放列表：文件命名不规范时失效。
  * 减少回传次数。**油猴脚本也需要更新**
  * 播放网络流时：pot 播放列表：降低添加条目速度，减少异常。
  * 播放网络流时：mpc 切换进度时 api 无响应，导致提前回传。
2023-09-04:
1. Trakt 播放记录单向同步。（详见 FAQ）
2. 剧集多版本：下一集匹配失败则禁用播放列表。
* 版本间累积更新：
  * 自动选择视频版本（限emby，配置文件有新增条目 [dev]）
  * 油猴：非管理员可显示文件名。
*/

let config = {
    logLevel: 2,
    disableOpenFolder: false, // false 改为 true 则禁用打开文件夹的按钮。
};

let fistTime = true;

let logger = {
    error: function (...args) {
        if (config.logLevel >= 1) {
            console.log('%cerror', 'color: yellow; font-style: italic; background-color: blue;', ...args);
        }
    },
    info: function (...args) {
        if (config.logLevel >= 2) {
            console.log('%cinfo', 'color: yellow; font-style: italic; background-color: blue;', ...args);
        }
    },
    debug: function (...args) {
        if (config.logLevel >= 3) {
            console.log('%cdebug', 'color: yellow; font-style: italic; background-color: blue;', ...args);
        }
    },
}

async function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function removeErrorWindows() {
    let okButtonList = document.querySelectorAll('button[data-id="ok"]');
    let state = false;
    for (let index = 0; index < okButtonList.length; index++) {
        const element = okButtonList[index];
        if (element.textContent.search(/(了解|好的|知道|Got It)/) != -1) {
            element.click();
            state = true;
        }
    }

    let jellyfinSpinner = document.querySelector('div.docspinner');
    if (jellyfinSpinner) {
        jellyfinSpinner.remove();
        state = true;
    };

    return state;
}

function switchLocalStorage(key, defaultValue = 'true', trueValue = 'true', falseValue = 'false') {
    if (key in localStorage) {
        let value = (localStorage.getItem(key) === trueValue) ? falseValue : trueValue;
        localStorage.setItem(key, value);
    } else {
        localStorage.setItem(key, defaultValue)
    }
    console.log('switchLocalStorage ', key, ' to ', localStorage.getItem(key))
}

function setModeSwitchMenu(storageKey, menuStart = '', menuEnd = '', defaultValue = '关闭', trueValue = '开启', falseValue = '关闭') {
    let switchNameMap = { 'true': trueValue, 'false': falseValue, null: defaultValue };
    let menuId = GM_registerMenuCommand(menuStart + switchNameMap[localStorage.getItem(storageKey)] + menuEnd, clickMenu);

    function clickMenu() {
        GM_unregisterMenuCommand(menuId);
        switchLocalStorage(storageKey)
        menuId = GM_registerMenuCommand(menuStart + switchNameMap[localStorage.getItem(storageKey)] + menuEnd, clickMenu);
    }

}

function sendDataToLocalServer(data, path) {
    let url = `http://127.0.0.1:58000/${path}/`;
    GM_xmlhttpRequest({
        method: 'POST',
        url: url,
        data: JSON.stringify(data),
        headers: {
            'Content-Type': 'application/json'
        },
    });
}

async function embyToLocalPlayer(playbackUrl, request, response) {
    let data = {
        ApiClient: ApiClient,
        fistTime: fistTime,
        playbackData: response,
        playbackUrl: playbackUrl,
        request: request,
        mountDiskEnable: localStorage.getItem('mountDiskEnable'),

    };
    sendDataToLocalServer(data, 'embyToLocalPlayer');
    for (const times of Array(15).keys()) {
        await sleep(200);
        if (removeErrorWindows()) {
            logger.info(`remove error window used time: ${(times + 1) * 0.2}`);
            break;
        };
    }
    fistTime = false;
}

function isHidden(el) {
    return (el.offsetParent === null);
}

function getVisibleElement(elList) {
    if (!elList) return;
    if (NodeList.prototype.isPrototypeOf(elList)) {
        for (let i = 0; i < elList.length; i++) {
            if (!isHidden(elList[i])) {
                return elList[i];
            }
        }
    } else {
        return elList;
    }
}

async function addOpenFolderElement() {
    if (config.disableOpenFolder) return;
    let mediaSources = null;
    for (const _ of Array(5).keys()) {
        await sleep(500);
        mediaSources = getVisibleElement(document.querySelectorAll('div.mediaSources'));
        if (mediaSources) break;
    }
    if (!mediaSources) return;
    let pathDiv = mediaSources.querySelector('div[class^="sectionTitle sectionTitle-cards"] > div');
    if (!pathDiv || pathDiv.className == 'mediaInfoItems' || pathDiv.id == 'addFileNameElement') return;
    let full_path = pathDiv.textContent;
    if (!full_path.match(/[/:]/)) return;
    if (full_path.match(/\d{1,3}\.?\d{0,2} (MB|GB)/)) return;

    let openButtonHtml = `<a id="openFolderButton" is="emby-linkbutton" class="raised item-tag-button 
    nobackdropfilter emby-button" ><i class="md-icon button-icon button-icon-left">link</i>Open Folder</a>`
    pathDiv.insertAdjacentHTML('beforebegin', openButtonHtml);
    let btn = mediaSources.querySelector('a#openFolderButton');
    btn.addEventListener("click", () => {
        logger.info(full_path);
        sendDataToLocalServer({ full_path: full_path }, 'openFolder');
    });
}

async function addFileNameElement(url, request) {
    let mediaSources = null;
    for (const _ of Array(5).keys()) {
        await sleep(500);
        mediaSources = getVisibleElement(document.querySelectorAll('div.mediaSources'));
        if (mediaSources) break;
    }
    if (!mediaSources) return;
    let pathDivs = mediaSources.querySelectorAll('div[class^="sectionTitle sectionTitle-cards"] > div');
    if (!pathDivs) return;
    pathDivs = Array.from(pathDivs);
    let _pathDiv = pathDivs[0];
    if (!/\d{4}\/\d+\/\d+/.test(_pathDiv.textContent)) return;
    if (_pathDiv.id == 'addFileNameElement') return;

    let response = await originFetch(url, request);
    let data = await response.json();
    data = data.MediaSources;

    for (let index = 0; index < pathDivs.length; index++) {
        const pathDiv = pathDivs[index];
        let filePath = data[index].Path;
        let fileName = filePath.split('\\').pop().split('/').pop();
        let fileDiv = `<div id="addFileNameElement">${fileName}</div> `
        pathDiv.insertAdjacentHTML('beforebegin', fileDiv);
    }
}

const originFetch = fetch;
unsafeWindow.fetch = async (url, request) => {
    if (url.indexOf('/PlaybackInfo?UserId') != -1) {
        if (url.indexOf('IsPlayback=true') != -1 && localStorage.getItem('webPlayerEnable') != 'true') {
            let response = await originFetch(url, request);
            let data = await response.clone().json();
            if (data.MediaSources[0].Path.search(/\Wbackdrop/i) == -1) {
                embyToLocalPlayer(url, request, data);
                return
            }
        } else {
            addOpenFolderElement();
            addFileNameElement(url, request);
        }
    } else if (url.indexOf('/Playing/Stopped') != -1 && localStorage.getItem('webPlayerEnable') != 'true') {
        return
    }
    return originFetch(url, request);
}

function initXMLHttpRequest() {
    const open = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (...args) {
        // 正常请求不匹配的网址
        let url = args[1]
        if (url.indexOf('playQueues?type=video') == -1) {
            return open.apply(this, args);
        }
        // 请求前拦截
        if (url.indexOf('playQueues?type=video') != -1
            && localStorage.getItem('webPlayerEnable') != 'true') {
            fetch(url, {
                method: args[0],
                headers: {
                    'Accept': 'application/json',
                }
            })
                .then(response => response.json())
                .then((res) => {
                    let data = {
                        playbackData: res,
                        playbackUrl: url,
                        mountDiskEnable: localStorage.getItem('mountDiskEnable'),

                    };
                    sendDataToLocalServer(data, 'plexToLocalPlayer');
                });
            return;
        }
        return open.apply(this, args);
    }
}

// 初始化请求并拦截 plex
initXMLHttpRequest()

setModeSwitchMenu('webPlayerEnable', '脚本在当前服务器 已', '', '启用', '禁用', '启用')
setModeSwitchMenu('mountDiskEnable', '读取硬盘模式已经 ')

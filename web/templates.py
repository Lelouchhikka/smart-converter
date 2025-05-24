HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>MediaMTX Monitor - Мониторинг БПЛА</title>
    <meta charset="utf-8">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; background-color: white; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h1 { color: #2c3e50; text-align: center; margin-bottom: 30px; }
        table { border-collapse: collapse; width: 100%; margin-top: 20px; }
        th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }
        th { background-color: #2c3e50; color: white; }
        tr:nth-child(even) { background-color: #f9f9f9; }
        .new-stream { background-color: #e6ffe6 !important; animation: highlight 2s ease-out; }
        .no-viewers { background-color: #fff3e6 !important; }
        .copy-btn { cursor: pointer; color: #3498db; margin-left: 8px; }
        .copy-btn:hover { color: #2980b9; }
        .status-indicator { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px; }
        .status-active { background-color: #2ecc71; }
        .status-inactive { background-color: #e74c3c; }
        .uptime { font-family: monospace; }
        @keyframes highlight { 0% { background-color: #e6ffe6; } 100% { background-color: transparent; } }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        .refresh-time { color: #7f8c8d; font-size: 0.9em; }
        .stream-count { background-color: #3498db; color: white; padding: 5px 10px; border-radius: 15px; font-size: 0.9em; }
    </style>
    <script>
        function copyToClipboard(text) {
            navigator.clipboard.writeText(text).then(() => {
                alert('Ссылка скопирована в буфер обмена!');
            });
        }
        function updateStreams() {
            fetch('/api/streams')
                .then(response => response.json())
                .then(data => {
                    const tbody = document.getElementById('streams-table').getElementsByTagName('tbody')[0];
                    tbody.innerHTML = '';
                    document.querySelector('.stream-count').textContent = `Активных потоков: ${data.length}`;
                    document.querySelector('.refresh-time').textContent = `Последнее обновление: ${new Date().toLocaleTimeString()}`;
                    data.forEach(stream => {
                        const row = tbody.insertRow();
                        row.className = stream.is_new ? 'new-stream' : (stream.readers === 0 ? 'no-viewers' : '');
                        const statusCell = row.insertCell();
                        const statusIndicator = document.createElement('span');
                        statusIndicator.className = `status-indicator ${stream.publishers > 0 ? 'status-active' : 'status-inactive'}`;
                        statusCell.appendChild(statusIndicator);
                        statusCell.appendChild(document.createTextNode(stream.publishers > 0 ? 'Активен' : 'Неактивен'));
                        const pathCell = row.insertCell();
                        pathCell.textContent = stream.path;
                        row.insertCell().textContent = stream.source_type;
                        row.insertCell().textContent = stream.publishers;
                        row.insertCell().textContent = stream.readers;
                        const uptimeCell = row.insertCell();
                        uptimeCell.className = 'uptime';
                        uptimeCell.textContent = stream.uptime;
                        const rtspCell = row.insertCell();
                        rtspCell.innerHTML = `${stream.rtsp_url} <span class="copy-btn" onclick="copyToClipboard('${stream.rtsp_url}')">📋</span>`;
                    });
                })
                .catch(error => {
                    console.error('Ошибка при обновлении данных:', error);
                });
        }
        setInterval(updateStreams, 5000);
        document.addEventListener('DOMContentLoaded', updateStreams);
    </script>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>MediaMTX Monitor - Мониторинг БПЛА</h1>
            <div>
                <span class="stream-count">Активных потоков: 0</span>
                <span class="refresh-time">Последнее обновление: -</span>
            </div>
        </div>
        <table id="streams-table">
            <thead>
                <tr>
                    <th>Статус</th>
                    <th>Путь</th>
                    <th>Тип источника</th>
                    <th>Издатели</th>
                    <th>Зрители</th>
                    <th>Время работы</th>
                    <th>RTSP URL</th>
                </tr>
            </thead>
            <tbody></tbody>
        </table>
    </div>
</body>
</html>
"""

STREAMS_VIEW_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Просмотр трансляций БПЛА</title>
    <meta charset="utf-8">
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; background-color: white; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h1 { color: #2c3e50; text-align: center; margin-bottom: 30px; }
        .streams-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 20px; margin-top: 20px; }
        .stream-card { background: #fff; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); overflow: hidden; }
        .stream-info { padding: 15px; background: #2c3e50; color: white; }
        .stream-title { margin: 0; font-size: 1.1em; font-weight: bold; }
        .stream-status { font-size: 0.9em; margin-top: 5px; }
        .stream-status.active { color: #2ecc71; }
        .stream-status.inactive { color: #e74c3c; }
        .video-container { position: relative; width: 100%; padding-top: 56.25%; }
        video { position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: #000; }
        .no-streams { text-align: center; padding: 40px; color: #7f8c8d; font-size: 1.2em; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        .refresh-time { color: #7f8c8d; font-size: 0.9em; }
        .stream-count { background-color: #3498db; color: white; padding: 5px 10px; border-radius: 15px; font-size: 0.9em; }
        .back-link { display: inline-block; margin-bottom: 20px; color: #3498db; text-decoration: none; }
        .back-link:hover { text-decoration: underline; }
    </style>
    <script>
        function updateStreams() {
            fetch('/api/streams')
                .then(response => response.json())
                .then(data => {
                    const container = document.querySelector('.streams-grid');
                    container.innerHTML = '';
                    document.querySelector('.stream-count').textContent = `Активных потоков: ${data.length}`;
                    document.querySelector('.refresh-time').textContent = `Последнее обновление: ${new Date().toLocaleTimeString()}`;
                    if (data.length === 0) {
                        container.innerHTML = '<div class="no-streams">Нет активных потоков</div>';
                        return;
                    }
                    data.forEach((stream, idx) => {
                        const card = document.createElement('div');
                        card.className = 'stream-card';
                        const info = document.createElement('div');
                        info.className = 'stream-info';
                        const title = document.createElement('div');
                        title.className = 'stream-title';
                        title.textContent = stream.path;
                        const status = document.createElement('div');
                        status.className = `stream-status ${stream.publishers > 0 ? 'active' : 'inactive'}`;
                        status.textContent = stream.publishers > 0 ? 'Активен' : 'Неактивен';
                        info.appendChild(title);
                        info.appendChild(status);
                        const videoContainer = document.createElement('div');
                        videoContainer.className = 'video-container';
                        const video = document.createElement('video');
                        video.controls = true;
                        video.autoplay = true;
                        video.id = 'video_' + idx;
                        videoContainer.appendChild(video);
                        card.appendChild(info);
                        card.appendChild(videoContainer);
                        container.appendChild(card);
                        if (Hls.isSupported()) {
                            var hls = new Hls();
                            hls.loadSource(stream.hls_url);
                            hls.attachMedia(video);
                        } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
                            video.src = stream.hls_url;
                        }
                    });
                })
                .catch(error => {
                    console.error('Ошибка при обновлении данных:', error);
                });
        }
        setInterval(updateStreams, 5000);
        document.addEventListener('DOMContentLoaded', updateStreams);
    </script>
</head>
<body>
    <div class="container">
        <a href="/" class="back-link">← Назад к мониторингу</a>
        <div class="header">
            <h1>Просмотр трансляций БПЛА</h1>
            <div>
                <span class="stream-count">Активных потоков: 0</span>
                <span class="refresh-time">Последнее обновление: -</span>
            </div>
        </div>
        <div class="streams-grid"></div>
    </div>
</body>
</html>
""" 
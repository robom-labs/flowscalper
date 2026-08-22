// 로컬 대시보드 개발·테스트 번들 구성을 정의한다.
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
    plugins: [react()],
    server: {
        host: '127.0.0.1',
        port: 5173,
        proxy: {
            '/api': 'http://127.0.0.1:8765',
            '/ws': { target: 'ws://127.0.0.1:8765', ws: true },
        },
    },
});

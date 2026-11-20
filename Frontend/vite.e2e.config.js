import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Explicitly isolated test app. Normal vite.config.js remains on port 5002.
export default defineConfig({
  plugins:[react()],
  define:{'import.meta.env.FYP_WORKFLOW_TARGET':JSON.stringify('e2e')},
  server:{host:'127.0.0.1',port:5180,strictPort:true,proxy:{'/api':{target:'http://127.0.0.1:5000',changeOrigin:false}}},
})

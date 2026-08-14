console.log('=== API CONFIG DEBUG ===');
console.log('NODE_ENV:', process.env.NODE_ENV);
console.log('REACT_APP_LOCAL_BUILD:', process.env.REACT_APP_LOCAL_BUILD);

// A local desktop build is served by the same FastAPI process as the API
// (see StaticFiles mount in app.py), so the frontend and backend share an
// origin — API calls can use relative paths and never leave localhost.
// This is set at build time (`REACT_APP_LOCAL_BUILD=true npm run build`),
// baked into the static bundle PyInstaller packages, and is separate from
// the existing hosted (Vercel + Render, cross-origin) build.
const API_BASE_URL = process.env.REACT_APP_LOCAL_BUILD === 'true'
  ? ''
  : process.env.NODE_ENV === 'production'
    ? 'https://ntu-add-drop-automator-v2.onrender.com'  // Your Render URL
    : 'http://localhost:5000';

console.log('Final API_BASE_URL:', API_BASE_URL || '(relative — same origin)');
console.log('=== END API CONFIG ===');

export default API_BASE_URL;

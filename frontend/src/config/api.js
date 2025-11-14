const API_BASE_URL = process.env.NODE_ENV === 'production' 
  ? 'https://ntu-add-drop-automator-v2-backend.onrender.com'  // Your Render URL
  : 'http://localhost:5000';

export default API_BASE_URL;
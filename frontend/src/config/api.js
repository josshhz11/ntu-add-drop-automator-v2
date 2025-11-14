const API_BASE_URL = process.env.NODE_ENV === 'production' 
  ? process.env.REACT_APP_API_URL || 'https://ntu-add-drop-automator-v2-backend.onrender.com'
  : 'http://localhost:5000';

export default API_BASE_URL;
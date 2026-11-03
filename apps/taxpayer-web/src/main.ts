import { createApp } from 'vue';
import App from './App.vue';
import { createPortalRouter } from './router';
import './styles.css';

createApp(App).use(createPortalRouter()).mount('#app');

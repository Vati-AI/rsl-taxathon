import { createApp } from 'vue';
import App from './App.vue';
import { createPortalRouter } from './router';
import './styles.css';
import '../../shared/design/tokens.css';

createApp(App).use(createPortalRouter()).mount('#app');

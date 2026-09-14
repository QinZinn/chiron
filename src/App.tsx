import { AppProvider } from './state/app';
import { useRoute, type Route } from './lib/route';
import { Sidebar } from './components/Sidebar';
import { AreaBoundary } from './components/ui';
import { ChatView } from './views/Chat';
import { FlashcardsView } from './views/Flashcards';
import { QuizView } from './views/Quiz';
import { KnowledgeView } from './views/Knowledge';
import { WeakView } from './views/Weak';
import { ScheduleView } from './views/Schedule';
import { SettingsView } from './views/Settings';

const AREA_NAME: Record<Route['view'], string> = {
  chat: 'Trò chuyện',
  flashcards: 'Thẻ ghi nhớ',
  quiz: 'Quiz',
  knowledge: 'Kiến thức',
  weak: 'Điểm yếu',
  schedule: 'Lịch học',
  settings: 'Cài đặt',
};

function View({ route }: { route: Route }) {
  switch (route.view) {
    case 'chat':
      return <ChatView sessionId={route.sessionId} />;
    case 'flashcards':
      return <FlashcardsView />;
    case 'quiz':
      return <QuizView />;
    case 'knowledge':
      return <KnowledgeView nodeId={route.nodeId} />;
    case 'weak':
      return <WeakView />;
    case 'schedule':
      return <ScheduleView />;
    case 'settings':
      return <SettingsView />;
  }
}

export function App() {
  const route = useRoute();
  return (
    <AppProvider>
      <div className="app">
        <AreaBoundary area="Thanh bên">
          <Sidebar route={route} />
        </AreaBoundary>
        {/* One boundary per area: a crash in one view leaves the sidebar and
            every other view usable. */}
        <AreaBoundary area={AREA_NAME[route.view]}>
          <View route={route} />
        </AreaBoundary>
      </div>
    </AppProvider>
  );
}

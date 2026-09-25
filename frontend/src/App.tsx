import { AppProvider } from './state/app';
import { useRoute, type Route } from './lib/route';
import { Sidebar } from './components/Sidebar';
import { AreaBoundary } from './components/ui';
import { ChatView } from './views/Chat';
import { FlashcardsView } from './views/Flashcards';
import { QuizView } from './views/Quiz';
import { KnowledgeView } from './views/Knowledge';
import { NotesView } from './views/Notes';
import { WeakView } from './views/Weak';
import { TodosView } from './views/Todos';
import { FeynmanView } from './views/Feynman';
import { SettingsView } from './views/Settings';

const AREA_NAME: Record<Route['view'], string> = {
  chat: 'Trò chuyện',
  flashcards: 'Thẻ ghi nhớ',
  quiz: 'Quiz',
  knowledge: 'Kiến thức',
  notes: 'Scan ghi chép',
  weak: 'Điểm yếu',
  todos: 'Việc cần ôn',
  feynman: 'Giảng lại',
  settings: 'Cài đặt',
};

function View({ route }: { route: Route }) {
  switch (route.view) {
    case 'chat':
      return <ChatView kind={route.kind} sessionId={route.sessionId} newSetId={route.newSetId} />;
    case 'flashcards':
      return <FlashcardsView />;
    case 'quiz':
      return <QuizView />;
    case 'knowledge':
      return <KnowledgeView nodeId={route.nodeId} />;
    case 'notes':
      return <NotesView key={route.noteId ?? 'list'} noteId={route.noteId} />;
    case 'weak':
      return <WeakView />;
    case 'todos':
      return <TodosView />;
    case 'feynman':
      return <FeynmanView />;
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

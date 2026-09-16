/**
 * Điểm yếu — COMING SOON, deliberately without sample data.
 *
 * Checked against the code: Mnemosyne writes weak_card_tasks /
 * weak_card_task_cards from POST /review (backend/src/weak_cards.rs), but no
 * route reads them (backend/src/main.rs registers none). Until one exists,
 * this screen shows the design's frame and says so, rather than inventing the
 * topics, mastery bars and stats the mockup (1c) shows.
 */
import { href } from '../lib/route';
import { ComingSoon, PageHeader } from '../components/ui';

export function WeakView() {
  return (
    <main className="main">
      <PageHeader title="Điểm yếu" />
      <div className="page">
        <div className="page-head">
          <div>
            <h2>Điểm yếu</h2>
            <p>Những chủ đề bạn chưa vững, tổng hợp từ thẻ ghi nhớ trượt.</p>
          </div>
        </div>
        <ComingSoon title="Mnemosyne chưa có endpoint để đọc điểm yếu">
          <p>
            Mnemosyne đã bắt đầu <em>ghi</em> điểm yếu: khi một thẻ bị chấm “Quên” từ 2 lần trong 5 lần ôn gần nhất,
            nó được thêm vào bảng <code>weak_card_tasks</code> và một task <code>@ontap</code> được tạo trong Todoist để
            Horae xếp lịch ôn.
          </p>
          <p>
            Nhưng hiện chưa có route nào để <em>đọc</em> bảng đó, nên màn này chưa có dữ liệu thật để hiển thị — và sẽ không
            hiển thị số liệu mẫu thay thế.
          </p>
          <p style={{ marginBottom: 0 }}>
            Trong lúc chờ, các thẻ đến hạn vẫn ôn được ở <a className="link" href={href({ view: 'flashcards' })}>Thẻ ghi nhớ</a>.
          </p>
        </ComingSoon>
      </div>
    </main>
  );
}

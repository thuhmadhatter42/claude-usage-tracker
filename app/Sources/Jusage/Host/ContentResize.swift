import Combine
import Foundation

/// Re-places the panel when its content may have changed height. `PanelController.place()` sizes the window from
/// the content's fitting height, so it has to run again after Today / Month / Settings, a new feed or a refusal
/// line changes the layout. Changes are announced before SwiftUI lays out, so the replace is deferred one turn
/// of the run loop (and bursts coalesce into one).
@MainActor
final class ContentResizeWatcher {
    private var subscription: AnyCancellable?
    private var pending = false

    init(changes: AnyPublisher<Void, Never>, replace: @escaping @MainActor () -> Void) {
        subscription = changes.sink { [weak self] _ in
            guard let self, !self.pending else { return }
            self.pending = true
            DispatchQueue.main.async { [weak self] in
                // SwiftUI lays the new content out on the same turn; place on the one after it.
                DispatchQueue.main.async {
                    MainActor.assumeIsolated {
                        self?.pending = false
                        replace()
                    }
                }
            }
        }
    }
}

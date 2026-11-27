import Foundation
import UserNotifications

/// S14: posts S8's notes (R-90 text, R-93). Permission is asked at launch, not one refresh later (N19). Each note
/// posts under its id `<folderKey>-<meterId>-<kind>`, so a repeat replaces the earlier banner. A denial or a failed
/// post is logged; the meters still colour.
protocol NotificationPosting {
    func requestAuthorization(_ done: @escaping (Bool, String?) -> Void)
    func post(id: String, title: String, body: String, _ done: @escaping (String?) -> Void)
}

/// Shows an alert even while jusage is the active app. Without this macOS drops a notification that arrives
/// while its app is in front, and jusage activates itself whenever the panel opens.
final class NotificationPresenter: NSObject, UNUserNotificationCenterDelegate {
    static let options: UNNotificationPresentationOptions = [.banner, .sound]
    static func present(_ handler: (UNNotificationPresentationOptions) -> Void) { handler(options) }

    /// Strong reference: `UNUserNotificationCenter.delegate` is weak.
    static let shared = NotificationPresenter()

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        Self.present(completionHandler)
    }
}

/// The system's notification centre. Only built inside the app bundle (`UNUserNotificationCenter` needs one).
struct SystemNotifications: NotificationPosting {
    func requestAuthorization(_ done: @escaping (Bool, String?) -> Void) {
        // Set before the first request so no alert can arrive without it.
        UNUserNotificationCenter.current().delegate = NotificationPresenter.shared
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { ok, error in
            done(ok, error?.localizedDescription)
        }
    }

    func post(id: String, title: String, body: String, _ done: @escaping (String?) -> Void) {
        UNUserNotificationCenter.current().getNotificationSettings { st in
            // `add` succeeds even when the user turned notifications off; that is not a delivery.
            if st.authorizationStatus == .denied { done("notifications are off for jusage in System Settings"); return }
            Self.add(id: id, title: title, body: body, done)
        }
    }

    private static func add(id: String, title: String, body: String, _ done: @escaping (String?) -> Void) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        let request = UNNotificationRequest(identifier: id, content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request) { error in done(error?.localizedDescription) }
    }
}

final class Notifier {
    private let center: NotificationPosting
    private let log: HostLog

    init(center: NotificationPosting, log: HostLog) {
        self.center = center; self.log = log
    }

    func requestPermission() {
        center.requestAuthorization { [log] ok, error in
            if let error { log.error("notify", "permission request failed: \(error)") }
            else if !ok { log.warn("notify", "notifications are off for jusage in System Settings; meters still colour, nothing will pop up") }
        }
    }

    /// Posts each note and reports, once all have answered, the ids the system actually accepted. The caller
    /// records only those as sent, so a failed post is tried again on the next run. May call back on any thread.
    func post(_ notes: [Note], completion: @escaping ([String]) -> Void = { _ in }) {
        if notes.isEmpty { completion([]); return }
        let lock = NSLock()
        var pending = notes.count
        var delivered: [String] = []
        for n in notes {
            center.post(id: n.id, title: n.title, body: n.body) { [log] error in
                if let error { log.error("notify", "could not post \(n.id): \(error)") }
                lock.lock()
                if error == nil { delivered.append(n.id) }
                pending -= 1
                let finished = pending == 0
                let result = delivered
                lock.unlock()
                if finished { completion(result) }
            }
        }
    }
}

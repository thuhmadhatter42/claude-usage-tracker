import Foundation
import UserNotifications

/// S14: posts S8's notes (R-90 text, R-93). Permission is asked at launch, not one refresh later (N19). Each note
/// posts under its id `<folderKey>-<meterId>-<kind>`, so a repeat replaces the earlier banner. A denial or a failed
/// post is logged; the meters still colour.
protocol NotificationPosting {
    func requestAuthorization(_ done: @escaping (Bool, String?) -> Void)
    func post(id: String, title: String, body: String, _ done: @escaping (String?) -> Void)
}

/// The system's notification centre. Only built inside the app bundle (`UNUserNotificationCenter` needs one).
struct SystemNotifications: NotificationPosting {
    func requestAuthorization(_ done: @escaping (Bool, String?) -> Void) {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { ok, error in
            done(ok, error?.localizedDescription)
        }
    }

    func post(id: String, title: String, body: String, _ done: @escaping (String?) -> Void) {
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

    func post(_ notes: [Note]) {
        for n in notes {
            center.post(id: n.id, title: n.title, body: n.body) { [log] error in
                if let error { log.error("notify", "could not post \(n.id): \(error)") }
            }
        }
    }
}

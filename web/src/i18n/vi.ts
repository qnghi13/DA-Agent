import type { Messages, Params } from "./en";

/** Tiếng Việt. Typed against `en`, so a missing or extra key fails the type check. */
export const vi: Messages = {
  locale: "vi-VN",
  language: {
    label: "Ngôn ngữ",
  },
  common: {
    loading: "Đang tải",
    cancel: "Huỷ",
    close: "Đóng",
    save: "Lưu",
    saving: "Đang lưu…",
    retry: "Thử lại",
  },
  account: {
    menu: "Tài khoản",
    operator: "Quản trị viên",
    workspace: "Không gian làm việc",
    operatorSession: "Phiên quản trị",
    workspaceMember: "Thành viên",
    inviteOperator: "Mời quản trị viên",
    inviteDevice: "Mời người khác hoặc thêm thiết bị",
    signOut: "Đăng xuất",
    inviteText: (operator: boolean) =>
      `Ai mở liên kết này trong vòng 15 phút sẽ đăng nhập vào ${operator ? "trang quản trị" : "không gian làm việc này"}. Liên kết chỉ dùng được một lần.`,
    link: "Liên kết đăng nhập",
    copied: "Đã sao chép",
    copy: "Sao chép liên kết",
    creating: "Đang tạo…",
    create: "Tạo liên kết đăng nhập",
  },
  errors: {
    signedOut: "Bạn đã đăng xuất.",
    slowDown: (text: string) => `Chậm lại một chút: ${text}`,
    codes: {
      not_found: (_: Params) => "Không tìm thấy.",
      "upload.too_large": (p: Params) => `Tệp ${p.file} quá lớn.`,
      "upload.too_many_files": (p: Params) => `Tối đa ${p.max} tệp.`,
      "upload.unsupported_type": (p: Params) =>
        `DA-Agent không đọc được loại tệp của ${p.file}. Hãy dùng một trong các loại: ${p.allowed}.`,
      "dataset.not_ready": (_: Params) => "Dữ liệu chưa sẵn sàng.",
      "dataset.changed": (_: Params) => "Dữ liệu đã thay đổi sau câu trả lời này. Hãy hỏi lại.",
      "export.expired": (_: Params) => "Tệp xuất đã hết hạn. Hãy tải lại.",
      "version.already_open": (_: Params) =>
        "Các tệp thêm trước đó vẫn đang được kiểm tra hoặc chờ duyệt. Hãy hoàn tất hoặc bỏ chúng trước.",
      "version.limit": (p: Params) => `Dữ liệu này đã có ${p.max} phiên bản, mức tối đa cho phép.`,
      "limit.sign_in": (_: Params) => "Đăng nhập sai quá nhiều lần từ địa chỉ này.",
      "limit.requests": (_: Params) => "Quá nhiều yêu cầu.",
      "limit.jobs": (_: Params) => "Quá nhiều tác vụ mới trong không gian làm việc này.",
      "limit.budget": (p: Params) => `Không gian làm việc đã dùng hết ngân sách model hôm nay ($${p.limit}).`,
    },
  },
};

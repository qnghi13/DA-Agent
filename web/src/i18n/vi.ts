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
  explain: {
    date: (day: number, month: number, year: number) => `${day}/${month}/${year}`,
    units: {
      day: "ngày",
      month: "tháng",
      year: "năm",
      quarter: "quý",
      hour: "giờ trong ngày",
      weekday: "thứ trong tuần",
      month_of_year: "tháng trong năm",
    },
    rows: "các dòng",
    allColumns: "tất cả các cột",
    count: (of: string | null, distinct: boolean) =>
      of ? `số ${of}${distinct ? " khác nhau" : ""}` : "số dòng",
    aggregate: (func: string, of: string, distinct: boolean) => {
      const subject = distinct ? `các ${of} khác nhau` : of;
      switch (func) {
        case "sum":
          return `tổng ${subject}`;
        case "avg":
          return `trung bình ${subject}`;
        case "min":
          return `${subject} nhỏ nhất`;
        case "max":
          return `${subject} lớn nhất`;
        case "median":
          return `trung vị ${subject}`;
        default:
          return `${func} ${subject}`;
      }
    },
    where: (text: string, conditions: string) => `${text} khi ${conditions}`,
    and: " và ",
    or: " hoặc ",
    matches: {
      ignore_case: " (không phân biệt hoa thường)",
      ignore_accents: " (không phân biệt dấu)",
      ignore_case_and_accents: " (không phân biệt hoa thường và dấu)",
    },
    comparisons: {
      equals: "là",
      not_equals: "khác",
      greater: "lớn hơn",
      at_least: "ít nhất là",
      less: "nhỏ hơn",
      at_most: "nhiều nhất là",
      starts_with: "bắt đầu bằng",
      ends_with: "kết thúc bằng",
      contains: "có chứa",
    },
    period: (subject: string, from: string, to: string) => `${subject} từ ${from} đến ${to}`,
    onOrAfter: (subject: string, from: string) => `${subject} từ ${from} trở đi`,
    onOrBefore: (subject: string, to: string) => `${subject} đến hết ${to}`,
    is: (subject: string, values: string) => `${subject} là ${values}`,
    isNot: (subject: string, values: string) => `${subject} không phải ${values}`,
    between: (subject: string, low: string, high: string) => `${subject} từ ${low} đến ${high}`,
    empty: (subject: string) => `${subject} trống`,
    notEmpty: (subject: string) => `${subject} không trống`,
    isYes: (subject: string) => `${subject} là có`,
    isNo: (subject: string) => `${subject} là không`,
    matchedWith: (table: string, on: string) => `ghép với ${table}${on ? ` theo ${on}` : ""}`,
    keptUnmatched: " (giữ cả dòng không ghép được)",
    top: (descending: boolean, limit: number, by: string) =>
      `${limit} dòng ${descending ? "cao" : "thấp"} nhất theo ${by}`,
    sorted: (by: string, descending: boolean) =>
      `sắp xếp theo ${by}, ${descending ? "cao" : "thấp"} nhất trước`,
    firstRows: (limit: number) => `${limit} dòng đầu tiên`,
    eachCombinationOnce: (columns: string) => `${columns}, mỗi tổ hợp một lần`,
    labels: {
      data: "Dữ liệu",
      calculated: "Phép tính",
      shows: "Hiển thị",
      rowsWhere: "Chỉ lấy dòng có",
      groupedBy: "Nhóm theo",
      groupsWhere: "Chỉ lấy nhóm có",
      shown: "Cách hiển thị",
    },
  },
  metrics: {
    onlyRowsWhere: (measure: string, filters: string) => `${measure}, chỉ tính dòng có ${filters}`,
    nameMissing: "Hãy đặt tên cho định nghĩa.",
    nameTooLong: (max: number) => `Tên dài tối đa ${max} ký tự.`,
    tooManyAliases: (max: number) => `Tối đa ${max} tên khác.`,
    aliasTooLong: (max: number) => `Mỗi tên khác dài tối đa ${max} ký tự.`,
  },
  run: {
    planning: "Đang lên kế hoạch",
    checking: "Đang kiểm tra câu trả lời",
    querying: (count: number) => `Đang truy vấn dữ liệu · ${count} truy vấn`,
    reading: "Đang đọc dữ liệu",
    queries: (count: number) => `${count} truy vấn`,
    stopped: "Đã dừng",
    failed: "Thất bại",
    verified: "Đã kiểm chứng",
    needsDetail: "Cần thêm chi tiết",
    noAnswer: "Không có câu trả lời",
    yes: "Có",
    no: "Không",
    tools: {
      describe_dataset: "Đọc mô tả các bảng",
      run_sql: "Truy vấn dữ liệu",
      make_chart: "Vẽ biểu đồ",
      search_rules: "Tra quy tắc nghiệp vụ",
      submit_answer: "Đưa ra câu trả lời",
    },
    stopReasons: {
      answered: "Đã trả lời",
      needs_clarification: "Agent cần thêm chi tiết để trả lời.",
      iteration_limit: "Đã dừng sau quá nhiều bước mà chưa có câu trả lời.",
      budget_exceeded: "Đã dừng: câu hỏi này chạm mức chi phí tối đa.",
      llm_error: "Không kết nối được tới mô hình ngôn ngữ.",
      invalid_tool_call: "Mô hình liên tục gọi công cụ sai cách.",
      no_tool_call: "Mô hình dừng mà không trả lời.",
      cancelled: "Bạn đã dừng câu hỏi này.",
      deadline_exceeded: "Đã dừng: câu hỏi này hết thời gian.",
    },
  },
  feedback: {
    reasons: {
      wrong_number: "Sai số liệu",
      misunderstood: "Trả lời một câu hỏi khác",
      should_abstain: "Lẽ ra nên nói là không xác định được",
      unclear: "Khó hiểu",
      other: "Lý do khác",
    },
  },
  status: {
    uploaded: "Đang đọc",
    proposed: "Chờ duyệt",
    building: "Đang dựng",
    ready: "Sẵn sàng",
    failed: "Lỗi",
  },
  profile: {
    mean: (value: string) => ` · trung bình ${value}`,
    noRepeats: (examples: string) => `không giá trị nào lặp lại, ví dụ ${examples}`,
  },
  suggestions: {
    generic: [
      "Tổng theo từng tháng là bao nhiêu?",
      "Mục nào xuất hiện nhiều nhất?",
      "Dữ liệu trải từ ngày nào đến ngày nào?",
    ],
    totalByMonth: (amount: string) => `Tổng ${amount} theo từng tháng là bao nhiêu?`,
    total: (amount: string) => `Tổng ${amount} là bao nhiêu?`,
    top5: (category: string, amount: string) => `5 ${category} nào có tổng ${amount} cao nhất?`,
    countByStatus: (status: string) => `Mỗi ${status} có bao nhiêu dòng?`,
    bestMonth: (amount: string) => `Tháng nào có tổng ${amount} cao nhất?`,
    dateRange: (date: string) => `${date} trải từ ngày nào đến ngày nào?`,
  },
  versions: {
    stuck: "Việc kiểm tra các tệp mới đã dừng giữa chừng. Hãy bỏ chúng và thêm lại.",
    checking: "Đang kiểm tra các tệp mới…",
    needsDecision: "Các tệp mới của bạn cần một quyết định",
    adding: "Đang thêm các tệp mới…",
    failed: (error: string | null) => `Không thêm được tệp: ${error ?? "không có thông tin chi tiết"}`,
    discarded: "Đã bỏ các tệp mới",
    tables: (count: number) => `${count} bảng`,
    added: (rows: string, table: string, rate: string | null) =>
      `Đã thêm ${rows} dòng vào ${table}${rate === null ? "" : ` (tỷ lệ đọc được ${rate}%)`}`,
    earlierNewFiles: (files: string) =>
      `Một số dòng này cũng có trong ${files}, được gửi cùng lần tải lên này.`,
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

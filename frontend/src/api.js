// Every request is a GraphQL operation on one endpoint; see docs/api.md.
const ENDPOINT = "/graphql/";

const COMMENT_FIELDS = `
  id
  parentId
  rootId
  username
  email
  homePage
  text
  attachment
  attachmentType
  createdAt
  replyCount
`;

export class ApiError extends Error {
  constructor(code, fields) {
    super(code);
    this.code = code;
    this.fields = fields || {};
  }
}

async function send(options) {
  const response = await fetch(ENDPOINT, { method: "POST", ...options });
  const payload = await response.json().catch(() => null);
  const error = payload?.errors?.[0];
  if (error) throw new ApiError(error.extensions?.code || "SERVER_ERROR", error.extensions?.fields);
  if (!response.ok || !payload?.data) throw new ApiError("SERVER_ERROR");
  return payload.data;
}

function request(query, variables = {}, headers = {}) {
  return send({
    headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify({ query, variables }),
  });
}

// The GraphQL multipart request format: the file travels beside the operation.
function requestWithFile(query, variables, fileVariable, file, headers) {
  const body = new FormData();
  body.append("operations", JSON.stringify({ query, variables: { ...variables, [fileVariable]: null } }));
  body.append("map", JSON.stringify({ 0: [`variables.${fileVariable}`] }));
  body.append("0", file);
  return send({ headers, body });
}

// "-created_at" -> CREATED_AT_DESC
export function orderingEnum(ordering) {
  const direction = ordering.startsWith("-") ? "DESC" : "ASC";
  return `${ordering.replace(/^-/, "").toUpperCase()}_${direction}`;
}

const CREATE_COMMENT = `
  mutation CreateComment($input: CommentInput!, $attachment: Upload) {
    createComment(input: $input, attachment: $attachment) {
      comment { ${COMMENT_FIELDS} }
      token
      tokenExpiresIn
    }
  }
`;

export const api = {
  listComments: async ({ page, ordering }) => {
    const data = await request(
      `query Comments($page: Int!, $ordering: CommentOrdering!) {
        comments(page: $page, ordering: $ordering) {
          count
          results { ${COMMENT_FIELDS} }
        }
      }`,
      { page, ordering: orderingEnum(ordering) },
    );
    return data.comments;
  },
  getReplies: async (parentId) => {
    const data = await request(
      `query Replies($parentId: ID!) {
        replies(parentId: $parentId) { ${COMMENT_FIELDS} }
      }`,
      { parentId },
    );
    return data.replies;
  },
  createComment: async (input, file, token) => {
    const headers = token ? { Authorization: `Bearer ${token}` } : {};
    const variables = { input, attachment: null };
    const data = file
      ? await requestWithFile(CREATE_COMMENT, variables, "attachment", file, headers)
      : await request(CREATE_COMMENT, variables, headers);
    return data.createComment;
  },
  previewText: async (text) => {
    const data = await request(`query Preview($text: String!) { previewText(text: $text) }`, { text });
    return data.previewText;
  },
  getCaptcha: async () => {
    const data = await request(`mutation Captcha { issueCaptcha { key image } }`);
    return data.issueCaptcha;
  },
};

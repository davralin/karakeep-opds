use std::{
    env,
    io::{Cursor, Read, Write},
    net::TcpStream,
    sync::Arc,
    time::Duration,
};

use axum::{
    Json, Router,
    body::Body,
    extract::{Path, Query, State},
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::{IntoResponse, Response},
    routing::get,
};
use base64::{Engine, engine::general_purpose::STANDARD as BASE64};
use chrono::{DateTime, SecondsFormat, Utc};
use ego_tree::NodeRef;
use reqwest::{Client, Url};
use scraper::{Html, node::Node};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use subtle::ConstantTimeEq;
use uuid::Uuid;
use zip::{CompressionMethod, ZipWriter, write::SimpleFileOptions};

const APP_VERSION: &str = env!("CARGO_PKG_VERSION");
const EPUB_MEDIA_TYPE: &str = "application/epub+zip";

#[derive(Clone)]
struct AppState {
    settings: Settings,
    client: Client,
}

#[derive(Clone, Debug)]
struct Settings {
    karakeep_api_base_url: Url,
    karakeep_api_token: String,
    opds_username: String,
    opds_password: String,
    opds_page_size: usize,
    service_base_url: Option<String>,
}

impl Settings {
    fn from_env() -> Result<Self, String> {
        let required = [
            "KARAKEEP_BASE_URL",
            "KARAKEEP_API_TOKEN",
            "OPDS_USERNAME",
            "OPDS_PASSWORD",
        ];
        let missing: Vec<_> = required
            .iter()
            .filter(|name| env::var(name).map(|value| value.is_empty()).unwrap_or(true))
            .copied()
            .collect();
        if !missing.is_empty() {
            return Err(format!(
                "Missing required environment variables: {}",
                missing.join(", ")
            ));
        }

        let page_size = env::var("OPDS_PAGE_SIZE")
            .unwrap_or_else(|_| "50".into())
            .parse::<usize>()
            .map_err(|_| "OPDS_PAGE_SIZE must be an integer".to_string())?;
        if !(1..=100).contains(&page_size) {
            return Err("OPDS_PAGE_SIZE must be between 1 and 100".into());
        }
        let log_level = env::var("LOG_LEVEL")
            .unwrap_or_else(|_| "INFO".into())
            .to_uppercase();
        if !matches!(
            log_level.as_str(),
            "ERROR" | "WARN" | "WARNING" | "INFO" | "DEBUG" | "TRACE"
        ) {
            return Err("LOG_LEVEL must be a valid log level".into());
        }

        let base = env::var("KARAKEEP_BASE_URL")
            .unwrap()
            .trim_end_matches('/')
            .to_string();
        let api_path =
            normalize_path(&env::var("KARAKEEP_API_PATH").unwrap_or_else(|_| "/api/v1".into()));
        let api_url = Url::parse(&format!("{base}{api_path}/"))
            .map_err(|_| "KARAKEEP_BASE_URL must be a valid URL".to_string())?;

        Ok(Self {
            karakeep_api_base_url: api_url,
            karakeep_api_token: env::var("KARAKEEP_API_TOKEN").unwrap(),
            opds_username: env::var("OPDS_USERNAME").unwrap(),
            opds_password: env::var("OPDS_PASSWORD").unwrap(),
            opds_page_size: page_size,
            service_base_url: env::var("SERVICE_BASE_URL")
                .ok()
                .filter(|value| !value.is_empty())
                .map(|value| value.trim_end_matches('/').to_string()),
        })
    }
}

fn normalize_path(value: &str) -> String {
    let path = value.trim().trim_matches('/');
    if path.is_empty() {
        String::new()
    } else {
        format!("/{path}")
    }
}

#[derive(Debug)]
struct AppError {
    status: StatusCode,
    message: &'static str,
}

impl AppError {
    fn bad_gateway(message: &'static str) -> Self {
        Self {
            status: StatusCode::BAD_GATEWAY,
            message,
        }
    }

    fn upstream_status(status: StatusCode, message: &'static str) -> Self {
        Self { status, message }
    }
}

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        (self.status, Json(json!({"detail": self.message}))).into_response()
    }
}

type AppResult<T> = Result<T, AppError>;

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Asset {
    #[serde(default)]
    id: String,
    #[serde(rename = "assetType", default)]
    asset_type: String,
}

#[derive(Clone, Debug, Deserialize, Default)]
struct Content {
    #[serde(default)]
    title: String,
    #[serde(default)]
    url: String,
    #[serde(default)]
    description: String,
    #[serde(rename = "htmlContent", default)]
    html_content: String,
    #[serde(default)]
    author: String,
    #[serde(default)]
    publisher: String,
    #[serde(rename = "contentAssetId", default)]
    content_asset_id: String,
    #[serde(rename = "imageAssetId", default)]
    image_asset_id: String,
    #[serde(rename = "screenshotAssetId", default)]
    screenshot_asset_id: String,
}

#[derive(Clone, Debug, Deserialize, Default)]
struct Tag {
    #[serde(default)]
    name: String,
}

#[derive(Clone, Debug, Deserialize)]
struct Bookmark {
    #[serde(default)]
    id: String,
    #[serde(default)]
    title: String,
    #[serde(rename = "createdAt", default)]
    created_at: String,
    #[serde(rename = "modifiedAt", default)]
    modified_at: String,
    #[serde(default)]
    tags: Vec<Tag>,
    #[serde(default)]
    assets: Vec<Asset>,
    #[serde(default)]
    content: Content,
}

impl Bookmark {
    fn title(&self) -> &str {
        if self.title.is_empty() {
            if self.content.title.is_empty() {
                "Untitled"
            } else {
                &self.content.title
            }
        } else {
            &self.title
        }
    }
    fn description(&self) -> &str {
        &self.content.description
    }
    fn updated_at(&self) -> &str {
        if self.modified_at.is_empty() {
            &self.created_at
        } else {
            &self.modified_at
        }
    }
    fn cover_asset_id(&self) -> &str {
        if self.content.image_asset_id.is_empty() {
            &self.content.screenshot_asset_id
        } else {
            &self.content.image_asset_id
        }
    }
    fn readable_content_asset_id(&self) -> Option<&str> {
        if !self.content.content_asset_id.is_empty() {
            return Some(&self.content.content_asset_id);
        }
        self.assets
            .iter()
            .find(|asset| asset.asset_type == "linkHtmlContent")
            .map(|asset| asset.id.as_str())
    }
    fn tag_names(&self) -> Vec<&str> {
        self.tags
            .iter()
            .filter_map(|tag| (!tag.name.is_empty()).then_some(tag.name.as_str()))
            .collect()
    }
}

#[derive(Debug, Deserialize)]
struct BookmarkPage {
    #[serde(default)]
    bookmarks: Vec<Bookmark>,
    #[serde(rename = "nextCursor", default)]
    next_cursor: String,
}

async fn karakeep_get(
    state: &AppState,
    path: &str,
    query: &[(&str, String)],
    accept: &str,
) -> AppResult<reqwest::Response> {
    let url = state
        .settings
        .karakeep_api_base_url
        .join(path)
        .map_err(|_| AppError::bad_gateway("Karakeep request failed"))?;
    state
        .client
        .get(url)
        .bearer_auth(&state.settings.karakeep_api_token)
        .header(header::ACCEPT, accept)
        .query(query)
        .send()
        .await
        .map_err(|_| AppError::bad_gateway("Karakeep request failed"))
}

async fn get_page(state: &AppState, cursor: &str, archived: bool) -> AppResult<BookmarkPage> {
    let mut query = vec![
        ("limit", state.settings.opds_page_size.to_string()),
        ("archived", archived.to_string()),
    ];
    if !cursor.is_empty() {
        query.push(("cursor", cursor.to_string()));
    }
    let response = karakeep_get(state, "bookmarks", &query, "application/json").await?;
    if !response.status().is_success() {
        return Err(AppError::bad_gateway("Karakeep bookmarks request failed"));
    }
    response
        .json()
        .await
        .map_err(|_| AppError::bad_gateway("Karakeep bookmarks request failed"))
}

async fn get_bookmark(state: &AppState, id: &str) -> AppResult<Bookmark> {
    let response = karakeep_get(state, &format!("bookmarks/{id}"), &[], "application/json").await?;
    if !response.status().is_success() {
        return Err(AppError::upstream_status(
            response.status(),
            "Bookmark not found",
        ));
    }
    response
        .json()
        .await
        .map_err(|_| AppError::bad_gateway("Karakeep bookmark request failed"))
}

async fn get_asset(state: &AppState, id: &str) -> AppResult<(Vec<u8>, String)> {
    let response = karakeep_get(state, &format!("assets/{id}"), &[], "*/*").await?;
    if !response.status().is_success() {
        return Err(AppError::upstream_status(
            response.status(),
            "Asset not found",
        ));
    }
    let media_type = response
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("application/octet-stream")
        .to_string();
    let bytes = response
        .bytes()
        .await
        .map_err(|_| AppError::bad_gateway("Karakeep asset request failed"))?;
    Ok((bytes.to_vec(), media_type))
}

fn authorized(headers: &HeaderMap, settings: &Settings) -> bool {
    let Some(value) = headers
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
    else {
        return false;
    };
    let Some(encoded) = value.strip_prefix("Basic ") else {
        return false;
    };
    let Ok(decoded) = BASE64.decode(encoded) else {
        return false;
    };
    let expected = format!("{}:{}", settings.opds_username, settings.opds_password);
    decoded.len() == expected.len() && decoded.ct_eq(expected.as_bytes()).into()
}

fn unauthorized_response() -> Response {
    let mut response = StatusCode::UNAUTHORIZED.into_response();
    response
        .headers_mut()
        .insert(header::WWW_AUTHENTICATE, HeaderValue::from_static("Basic"));
    response
}

async fn healthz() -> Json<Value> {
    Json(json!({"status": "ok"}))
}

async fn root(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    request: axum::extract::Request,
) -> Response {
    if !authorized(&headers, &state.settings) {
        return unauthorized_response();
    }
    atom_response(navigation_feed(&external_base_url(
        &request,
        &state.settings,
    )))
}

async fn root_json(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    request: axum::extract::Request,
) -> Response {
    if !authorized(&headers, &state.settings) {
        return unauthorized_response();
    }
    Json(render_opds2(&navigation_feed(&external_base_url(
        &request,
        &state.settings,
    ))))
    .into_response()
}

#[derive(Deserialize, Default)]
struct CursorQuery {
    #[serde(default)]
    cursor: String,
}

async fn bookmarks_atom(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Query(query): Query<CursorQuery>,
    request: axum::extract::Request,
) -> Response {
    if !authorized(&headers, &state.settings) {
        return unauthorized_response();
    }
    match get_page(&state, &query.cursor, false).await {
        Ok(page) => atom_response(bookmarks_feed(
            page,
            &external_base_url(&request, &state.settings),
            &query.cursor,
            "bookmarks",
            "Karakeep Bookmarks",
        )),
        Err(error) => error.into_response(),
    }
}

async fn bookmarks_json(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Query(query): Query<CursorQuery>,
    request: axum::extract::Request,
) -> Response {
    if !authorized(&headers, &state.settings) {
        return unauthorized_response();
    }
    match get_page(&state, &query.cursor, false).await {
        Ok(page) => Json(render_opds2(&bookmarks_feed(
            page,
            &external_base_url(&request, &state.settings),
            &query.cursor,
            "bookmarks",
            "Karakeep Bookmarks",
        )))
        .into_response(),
        Err(error) => error.into_response(),
    }
}

async fn archived_atom(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Query(query): Query<CursorQuery>,
    request: axum::extract::Request,
) -> Response {
    if !authorized(&headers, &state.settings) {
        return unauthorized_response();
    }
    match get_page(&state, &query.cursor, true).await {
        Ok(page) => atom_response(bookmarks_feed(
            page,
            &external_base_url(&request, &state.settings),
            &query.cursor,
            "archived",
            "Karakeep Archived",
        )),
        Err(error) => error.into_response(),
    }
}

async fn archived_json(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Query(query): Query<CursorQuery>,
    request: axum::extract::Request,
) -> Response {
    if !authorized(&headers, &state.settings) {
        return unauthorized_response();
    }
    match get_page(&state, &query.cursor, true).await {
        Ok(page) => Json(render_opds2(&bookmarks_feed(
            page,
            &external_base_url(&request, &state.settings),
            &query.cursor,
            "archived",
            "Karakeep Archived",
        )))
        .into_response(),
        Err(error) => error.into_response(),
    }
}

async fn epub(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(path): Path<String>,
) -> Response {
    if !authorized(&headers, &state.settings) {
        return unauthorized_response();
    }
    let Some(id) = path.strip_suffix(".epub") else {
        return StatusCode::NOT_FOUND.into_response();
    };
    let bookmark = match get_bookmark(&state, id).await {
        Ok(bookmark) => bookmark,
        Err(error) => return error.into_response(),
    };
    let mut asset_content = String::new();
    if let Some(asset_id) = bookmark.readable_content_asset_id()
        && let Ok((content, media_type)) = get_asset(&state, asset_id).await
        && matches!(
            media_type.split(';').next(),
            Some("text/html") | Some("application/xhtml+xml")
        )
    {
        asset_content = String::from_utf8_lossy(&content).into_owned();
    }
    let bytes = match build_epub(&bookmark, &asset_content) {
        Ok(bytes) => bytes,
        Err(_) => return AppError::bad_gateway("Failed to generate EPUB").into_response(),
    };
    let filename = safe_filename(bookmark.title());
    let mut response = (
        [
            (header::CONTENT_TYPE, EPUB_MEDIA_TYPE),
            (
                header::CONTENT_DISPOSITION,
                &format!("attachment; filename=\"{filename}.epub\""),
            ),
        ],
        Body::from(bytes),
    )
        .into_response();
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static(EPUB_MEDIA_TYPE),
    );
    response
}

async fn asset(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Response {
    if !authorized(&headers, &state.settings) {
        return unauthorized_response();
    }
    match get_asset(&state, &id).await {
        Ok((content, media_type)) => {
            let mut response = Body::from(content).into_response();
            response.headers_mut().insert(
                header::CONTENT_TYPE,
                HeaderValue::from_str(&media_type)
                    .unwrap_or(HeaderValue::from_static("application/octet-stream")),
            );
            response
        }
        Err(error) => error.into_response(),
    }
}

#[derive(Clone)]
struct Link {
    href: String,
    rel: String,
    media_type: String,
    title: String,
}
#[derive(Clone)]
struct Entry {
    id: String,
    title: String,
    updated: String,
    summary: String,
    authors: Vec<String>,
    tags: Vec<String>,
    links: Vec<Link>,
}
#[derive(Clone)]
struct Feed {
    id: String,
    title: String,
    updated: String,
    links: Vec<Link>,
    entries: Vec<Entry>,
}

fn now_iso() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Secs, true)
}
fn link(href: String, rel: &str, media_type: &str, title: &str) -> Link {
    Link {
        href,
        rel: rel.into(),
        media_type: media_type.into(),
        title: title.into(),
    }
}
fn catalog_type() -> &'static str {
    "application/atom+xml;profile=opds-catalog;kind=acquisition"
}
fn navigation_feed(base: &str) -> Feed {
    Feed {
        id: format!("{base}/opds"),
        title: "Karakeep".into(),
        updated: now_iso(),
        links: vec![
            link(
                format!("{base}/opds.atom"),
                "self",
                catalog_type(),
                "OPDS 1.2",
            ),
            link(
                format!("{base}/opds.json"),
                "alternate",
                "application/opds+json",
                "OPDS 2",
            ),
        ],
        entries: vec![
            Entry {
                id: format!("{base}/opds/bookmarks.atom"),
                title: "Bookmarks/".into(),
                updated: now_iso(),
                summary: "Unread Karakeep bookmarks as on-demand EPUB publications.".into(),
                authors: vec![],
                tags: vec![],
                links: vec![
                    link(
                        format!("{base}/opds/bookmarks.atom"),
                        "subsection",
                        catalog_type(),
                        "OPDS 1.2",
                    ),
                    link(
                        format!("{base}/opds/bookmarks.json"),
                        "alternate",
                        "application/opds+json",
                        "OPDS 2",
                    ),
                ],
            },
            Entry {
                id: format!("{base}/opds/archived.atom"),
                title: "Archived/".into(),
                updated: now_iso(),
                summary: "Archived Karakeep bookmarks as on-demand EPUB publications.".into(),
                authors: vec![],
                tags: vec![],
                links: vec![
                    link(
                        format!("{base}/opds/archived.atom"),
                        "subsection",
                        catalog_type(),
                        "OPDS 1.2",
                    ),
                    link(
                        format!("{base}/opds/archived.json"),
                        "alternate",
                        "application/opds+json",
                        "OPDS 2",
                    ),
                ],
            },
        ],
    }
}

fn bookmarks_feed(page: BookmarkPage, base: &str, cursor: &str, path: &str, title: &str) -> Feed {
    let mut links = vec![
        link(
            format!("{base}/opds/{path}.atom"),
            "start",
            catalog_type(),
            "First page",
        ),
        link(
            format!("{base}/opds/{path}.atom"),
            "self",
            catalog_type(),
            "Current page",
        ),
        link(
            format!("{base}/opds/{path}.json"),
            "alternate",
            "application/opds+json",
            "OPDS 2",
        ),
    ];
    if !page.next_cursor.is_empty() {
        links.push(link(
            format!("{base}/opds/{path}.atom?cursor={}", page.next_cursor),
            "next",
            catalog_type(),
            "Next page",
        ));
    }
    Feed {
        id: if cursor.is_empty() {
            format!("{base}/opds/{path}.atom")
        } else {
            format!("{base}/opds/{path}.atom?cursor={cursor}")
        },
        title: if cursor.is_empty() {
            title.into()
        } else {
            format!("{title} (continued)")
        },
        updated: now_iso(),
        links,
        entries: page
            .bookmarks
            .iter()
            .map(|bookmark| bookmark_entry(bookmark, base))
            .collect(),
    }
}

fn bookmark_entry(bookmark: &Bookmark, base: &str) -> Entry {
    let mut links = vec![link(
        format!("{base}/opds/bookmarks/{}.epub", bookmark.id),
        "http://opds-spec.org/acquisition",
        EPUB_MEDIA_TYPE,
        "EPUB",
    )];
    if !bookmark.content.url.is_empty() {
        links.push(link(
            bookmark.content.url.clone(),
            "alternate",
            "text/html",
            "Source",
        ));
    }
    if !bookmark.cover_asset_id().is_empty() {
        links.push(link(
            format!("{base}/opds/assets/{}", bookmark.cover_asset_id()),
            "http://opds-spec.org/image",
            "image/*",
            "Cover",
        ));
    }
    Entry {
        id: format!("karakeep:{}", bookmark.id),
        title: bookmark.title().into(),
        updated: if bookmark.updated_at().is_empty() {
            now_iso()
        } else {
            bookmark.updated_at().into()
        },
        summary: bookmark.description().into(),
        authors: [
            bookmark.content.author.as_str(),
            bookmark.content.publisher.as_str(),
        ]
        .into_iter()
        .filter(|value| !value.is_empty())
        .map(String::from)
        .collect(),
        tags: bookmark.tag_names().into_iter().map(String::from).collect(),
        links,
    }
}

fn atom_response(feed: Feed) -> Response {
    (
        [(
            header::CONTENT_TYPE,
            "application/atom+xml;profile=opds-catalog;kind=acquisition",
        )],
        render_opds1(&feed),
    )
        .into_response()
}

fn escape_xml(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}
fn render_link(link: &Link) -> String {
    format!(
        r#"<link href="{}" rel="{}" type="{}"{} />"#,
        escape_xml(&link.href),
        escape_xml(&link.rel),
        escape_xml(&link.media_type),
        if link.title.is_empty() {
            String::new()
        } else {
            format!(r#" title="{}""#, escape_xml(&link.title))
        }
    )
}
fn render_opds1(feed: &Feed) -> String {
    let mut xml = format!(
        r#"<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:opds="http://opds-spec.org/2010/catalog"><id>{}</id><title>{}</title><updated>{}</updated>"#,
        escape_xml(&feed.id),
        escape_xml(&feed.title),
        escape_xml(&feed.updated)
    );
    for link in &feed.links {
        xml.push_str(&render_link(link));
    }
    for entry in &feed.entries {
        xml.push_str(&format!(
            "<entry><id>{}</id><title>{}</title><updated>{}</updated>",
            escape_xml(&entry.id),
            escape_xml(&entry.title),
            escape_xml(&entry.updated)
        ));
        if !entry.summary.is_empty() {
            xml.push_str(&format!(
                "<summary>{}</summary>",
                escape_xml(&entry.summary)
            ));
        }
        for author in &entry.authors {
            xml.push_str(&format!(
                "<author><name>{}</name></author>",
                escape_xml(author)
            ));
        }
        for tag in &entry.tags {
            xml.push_str(&format!(r#"<category term="{}" />"#, escape_xml(tag)));
        }
        for link in &entry.links {
            xml.push_str(&render_link(link));
        }
        xml.push_str("</entry>");
    }
    xml.push_str("</feed>");
    xml
}

fn render_opds2(feed: &Feed) -> Value {
    json!({"metadata": {"title": feed.title, "modified": feed.updated}, "links": feed.links.iter().map(opds2_link).collect::<Vec<_>>(), "publications": feed.entries.iter().map(|entry| json!({"metadata": {"title": entry.title, "modified": entry.updated, "description": entry.summary, "author": entry.authors.iter().map(|name| json!({"name": name})).collect::<Vec<_>>(), "subject": entry.tags}, "links": entry.links.iter().map(opds2_link).collect::<Vec<_>>() })).collect::<Vec<_>>()})
}
fn opds2_link(link: &Link) -> Value {
    json!({"href": link.href, "rel": link.rel, "type": link.media_type, "title": link.title})
}

fn external_base_url(request: &axum::extract::Request, settings: &Settings) -> String {
    settings.service_base_url.clone().unwrap_or_else(|| {
        let host = request
            .headers()
            .get(header::HOST)
            .and_then(|value| value.to_str().ok())
            .unwrap_or("localhost");
        format!("http://{host}")
    })
}
fn safe_filename(value: &str) -> String {
    let result: String = value
        .chars()
        .map(|character| {
            if character.is_alphanumeric() || matches!(character, '-' | '_') {
                character
            } else {
                '-'
            }
        })
        .collect();
    result
        .trim_matches('-')
        .chars()
        .take(80)
        .collect::<String>()
        .if_empty("bookmark")
}
trait IfEmpty {
    fn if_empty(self, fallback: &str) -> String;
}
impl IfEmpty for String {
    fn if_empty(self, fallback: &str) -> String {
        if self.is_empty() {
            fallback.into()
        } else {
            self
        }
    }
}

const VOID_TAGS: &[&str] = &["br", "hr"];
const ALLOWED_TAGS: &[&str] = &[
    "a",
    "b",
    "blockquote",
    "caption",
    "code",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
    "br",
];
const SUPPRESSED_TAGS: &[&str] = &[
    "audio", "embed", "form", "iframe", "object", "script", "style", "svg", "video",
];

fn render_html_node(node: NodeRef<'_, Node>, suppressed: bool, output: &mut String) {
    match node.value() {
        Node::Text(text) if !suppressed => output.push_str(&escape_xml(text)),
        Node::Element(element) => {
            let tag = element.name.local.as_ref();
            let hidden = suppressed || SUPPRESSED_TAGS.contains(&tag);
            if tag == "img" && !hidden {
                if let Some(alt) = element.attr("alt").filter(|value| !value.is_empty()) {
                    output.push_str(&escape_xml(&format!("[Image: {alt}]")));
                }
                return;
            }
            let allowed = ALLOWED_TAGS.contains(&tag);
            let mut attributes = String::new();
            if allowed
                && tag == "a"
                && let Some(href) = element.attr("href").filter(|href| {
                    href.starts_with("https://")
                        || href.starts_with("http://")
                        || href.starts_with("mailto:")
                })
            {
                attributes = format!(r#" href="{}""#, escape_xml(href));
            }
            if allowed && !hidden {
                if VOID_TAGS.contains(&tag) {
                    output.push_str(&format!("<{tag}{attributes} />"));
                    return;
                }
                output.push_str(&format!("<{tag}{attributes}>"));
            }
            for child in node.children() {
                render_html_node(child, hidden, output);
            }
            if allowed && !hidden && !VOID_TAGS.contains(&tag) {
                output.push_str(&format!("</{tag}>"));
            }
        }
        _ => {
            for child in node.children() {
                render_html_node(child, suppressed, output);
            }
        }
    }
}

fn text_first_xhtml(source: &str) -> String {
    let html = Html::parse_fragment(source);
    let mut result = String::new();
    for child in html.tree.root().children() {
        render_html_node(child, false, &mut result);
    }
    result
}

fn build_epub(bookmark: &Bookmark, asset_content: &str) -> Result<Vec<u8>, zip::result::ZipError> {
    let title = bookmark.title();
    let source = if asset_content.is_empty() {
        &bookmark.content.html_content
    } else {
        asset_content
    };
    let body = if source.is_empty() {
        let source_link = if bookmark.content.url.is_empty() {
            String::new()
        } else {
            format!(
                r#"<p>Source: <a href="{}">{}</a></p>"#,
                escape_xml(&bookmark.content.url),
                escape_xml(&bookmark.content.url)
            )
        };
        format!("<p>No readable article content was available for this bookmark.</p>{source_link}")
    } else {
        let rendered = text_first_xhtml(source);
        if rendered.trim().is_empty() {
            "<p>No readable article content was available for this bookmark.</p>".into()
        } else {
            rendered
        }
    };
    let author = if bookmark.content.author.is_empty() {
        if bookmark.content.publisher.is_empty() {
            "Karakeep"
        } else {
            &bookmark.content.publisher
        }
    } else {
        &bookmark.content.author
    };
    let modified = DateTime::parse_from_rfc3339(bookmark.updated_at())
        .map(|value| value.with_timezone(&Utc))
        .unwrap_or_else(|_| Utc::now())
        .to_rfc3339_opts(SecondsFormat::Secs, true);
    let uuid = Uuid::new_v5(
        &Uuid::NAMESPACE_URL,
        format!("karakeep:{}", bookmark.id).as_bytes(),
    );
    let mut bytes = Cursor::new(Vec::new());
    let mut zip = ZipWriter::new(&mut bytes);
    let stored = SimpleFileOptions::default().compression_method(CompressionMethod::Stored);
    zip.start_file("mimetype", stored)?;
    zip.write_all(b"application/epub+zip")?;
    let deflated = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);
    let files = [
        ("META-INF/container.xml", "<?xml version=\"1.0\" encoding=\"UTF-8\"?><container version=\"1.0\" xmlns=\"urn:oasis:names:tc:opendocument:xmlns:container\"><rootfiles><rootfile full-path=\"OEBPS/content.opf\" media-type=\"application/oebps-package+xml\" /></rootfiles></container>".to_string()),
        ("OEBPS/style.css", "body { font-family: serif; line-height: 1.45; margin: 5%; } pre, code { font-family: monospace; white-space: pre-wrap; } table { border-collapse: collapse; width: 100%; } th, td { border: 1px solid #777; padding: 0.35em; vertical-align: top; }".to_string()),
        ("OEBPS/content.opf", format!("<?xml version=\"1.0\" encoding=\"UTF-8\"?><package xmlns=\"http://www.idpf.org/2007/opf\" version=\"3.0\" unique-identifier=\"book-id\"><metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\"><dc:identifier id=\"book-id\">urn:uuid:{uuid}</dc:identifier><dc:title>{}</dc:title><dc:creator>{}</dc:creator><dc:language>en</dc:language><meta property=\"dcterms:modified\">{modified}</meta></metadata><manifest><item id=\"style\" href=\"style.css\" media-type=\"text/css\" /><item id=\"nav\" href=\"nav.xhtml\" media-type=\"application/xhtml+xml\" properties=\"nav\" /><item id=\"article\" href=\"article.xhtml\" media-type=\"application/xhtml+xml\" /></manifest><spine><itemref idref=\"article\" /></spine></package>", escape_xml(title), escape_xml(author))),
        ("OEBPS/nav.xhtml", format!("<?xml version=\"1.0\" encoding=\"UTF-8\"?><!DOCTYPE html><html xmlns=\"http://www.w3.org/1999/xhtml\" xmlns:epub=\"http://www.idpf.org/2007/ops\"><head><title>{}</title><link rel=\"stylesheet\" type=\"text/css\" href=\"style.css\" /></head><body><nav epub:type=\"toc\"><ol><li><a href=\"article.xhtml\">{}</a></li></ol></nav></body></html>", escape_xml(title), escape_xml(title))),
        ("OEBPS/article.xhtml", format!("<?xml version=\"1.0\" encoding=\"UTF-8\"?><!DOCTYPE html><html xmlns=\"http://www.w3.org/1999/xhtml\"><head><title>{}</title><meta charset=\"utf-8\" /><link rel=\"stylesheet\" type=\"text/css\" href=\"style.css\" /></head><body><h1>{}</h1>{body}</body></html>", escape_xml(title), escape_xml(title))),
    ];
    for (path, content) in files {
        zip.start_file(path, deflated)?;
        zip.write_all(content.as_bytes())?;
    }
    zip.finish()?;
    Ok(bytes.into_inner())
}

fn app(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/healthz", get(healthz))
        .route("/", get(root))
        .route("/opds", get(root))
        .route("/opds/", get(root))
        .route("/opds.atom", get(root))
        .route("/opds.json", get(root_json))
        .route("/opds/bookmarks.atom", get(bookmarks_atom))
        .route("/opds/bookmarks.json", get(bookmarks_json))
        .route("/opds/archived.atom", get(archived_atom))
        .route("/opds/archived.json", get(archived_json))
        .route("/opds/bookmarks/{*path}", get(epub))
        .route("/opds/assets/{id}", get(asset))
        .with_state(state)
}

fn healthcheck() -> Result<(), String> {
    let mut stream =
        TcpStream::connect_timeout(&"127.0.0.1:8000".parse().unwrap(), Duration::from_secs(3))
            .map_err(|error| error.to_string())?;
    stream
        .set_read_timeout(Some(Duration::from_secs(3)))
        .map_err(|error| error.to_string())?;
    stream
        .write_all(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .map_err(|error| error.to_string())?;
    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|error| error.to_string())?;
    if response.starts_with("HTTP/1.1 200") {
        Ok(())
    } else {
        Err("health endpoint did not return 200".into())
    }
}

#[tokio::main]
async fn main() {
    if env::args().nth(1).as_deref() == Some("healthcheck") {
        if let Err(error) = healthcheck() {
            eprintln!("healthcheck: {error}");
            std::process::exit(1);
        }
        return;
    }
    let settings = match Settings::from_env() {
        Ok(settings) => settings,
        Err(error) => {
            eprintln!("configuration: {error}");
            std::process::exit(1);
        }
    };
    let client = match Client::builder()
        .timeout(Duration::from_secs(30))
        .redirect(reqwest::redirect::Policy::limited(10))
        .user_agent(format!("karakeep-opds/{APP_VERSION}"))
        .build()
    {
        Ok(client) => client,
        Err(error) => {
            eprintln!("HTTP client: {error}");
            std::process::exit(1);
        }
    };
    let state = Arc::new(AppState { settings, client });
    let listener = match tokio::net::TcpListener::bind("0.0.0.0:8000").await {
        Ok(listener) => listener,
        Err(error) => {
            eprintln!("listen: {error}");
            std::process::exit(1);
        }
    };
    if let Err(error) = axum::serve(listener, app(state))
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await
    {
        eprintln!("server: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use zip::ZipArchive;

    fn bookmark() -> Bookmark {
        serde_json::from_value(json!({
            "id": "abc", "title": "Fish & Chips", "modifiedAt": "2024-01-01T01:02:03.123+01:00",
            "content": {"url": "https://example.test/article", "htmlContent": "<p>Hello</p>"}
        }))
        .unwrap()
    }

    #[test]
    fn text_first_xhtml_removes_active_content_and_escapes_text() {
        let xhtml = text_first_xhtml(
            "<h2>Fish & chips</h2><p onclick=x>A <strong>book</strong><br><script>bad()</script><a href=\"https://example.test\">good</a><a href=\"/bad\">plain</a><img alt=\"cover\">",
        );
        assert!(xhtml.contains("Fish &amp; chips"));
        assert!(xhtml.contains("<strong>book</strong>"));
        assert!(xhtml.contains("<br />"));
        assert!(xhtml.contains("<a href=\"https://example.test\">good</a>"));
        assert!(xhtml.contains("plain"));
        assert!(!xhtml.contains("href=\"/bad\""));
        assert!(xhtml.contains("[Image: cover]"));
        assert!(!xhtml.contains("bad()"));
        assert!(!xhtml.contains("onclick"));
    }

    #[test]
    fn epub_has_required_ordered_files_and_normalized_timestamp() {
        let content = build_epub(&bookmark(), "<p>Readable & body<br>").unwrap();
        let mut archive = ZipArchive::new(Cursor::new(content)).unwrap();
        assert_eq!(
            archive.file_names().next().transpose().unwrap().as_deref(),
            Some("mimetype")
        );
        let mimetype = archive.by_name("mimetype").unwrap();
        assert_eq!(mimetype.compression(), CompressionMethod::Stored);
        drop(mimetype);
        let article = {
            let mut file = archive.by_name("OEBPS/article.xhtml").unwrap();
            let mut text = String::new();
            file.read_to_string(&mut text).unwrap();
            text
        };
        assert!(article.contains("Readable &amp; body"));
        assert!(article.contains("<br />"));
        let package = {
            let mut file = archive.by_name("OEBPS/content.opf").unwrap();
            let mut text = String::new();
            file.read_to_string(&mut text).unwrap();
            text
        };
        assert!(package.contains("2024-01-01T00:02:03Z"));
    }

    #[test]
    fn basic_auth_requires_exact_credentials() {
        let settings = Settings {
            karakeep_api_base_url: Url::parse("https://example.test/api/").unwrap(),
            karakeep_api_token: "token".into(),
            opds_username: "reader".into(),
            opds_password: "secret".into(),
            opds_page_size: 50,
            service_base_url: None,
        };
        let mut headers = HeaderMap::new();
        headers.insert(
            header::AUTHORIZATION,
            HeaderValue::from_static("Basic cmVhZGVyOnNlY3JldA=="),
        );
        assert!(authorized(&headers, &settings));
        headers.insert(
            header::AUTHORIZATION,
            HeaderValue::from_static("Basic cmVhZGVyOndyb25n"),
        );
        assert!(!authorized(&headers, &settings));
    }

    #[test]
    fn atom_links_have_well_formed_attributes() {
        let feed = navigation_feed("https://opds.example");
        let xml = render_opds1(&feed);
        assert!(xml.contains("title=\"OPDS 1.2\""));
        assert!(xml.contains("<feed"));
    }

    #[test]
    fn bookmark_uses_content_and_asset_fallbacks() {
        let bookmark: Bookmark = serde_json::from_value(json!({
            "id": "abc",
            "assets": [{"id": "readable", "assetType": "linkHtmlContent"}],
            "content": {
                "title": "Content title",
                "screenshotAssetId": "screenshot"
            },
            "tags": [{"name": "reading"}, {"name": ""}]
        }))
        .unwrap();

        assert_eq!(bookmark.title(), "Content title");
        assert_eq!(bookmark.cover_asset_id(), "screenshot");
        assert_eq!(bookmark.readable_content_asset_id(), Some("readable"));
        assert_eq!(bookmark.tag_names(), vec!["reading"]);
    }

    #[test]
    fn paths_and_filenames_are_normalized() {
        assert_eq!(normalize_path(" /api/v1/ "), "/api/v1");
        assert_eq!(normalize_path("///"), "");
        assert_eq!(safe_filename("../"), "bookmark");
        assert_eq!(safe_filename("Hello, world!"), "Hello--world");
    }

    #[test]
    fn empty_epub_uses_an_escaped_source_link() {
        let mut bookmark = bookmark();
        bookmark.content.html_content.clear();
        bookmark.content.url = "https://example.test/?a=fish&b=chips".into();
        let content = build_epub(&bookmark, "").unwrap();
        let mut archive = ZipArchive::new(Cursor::new(content)).unwrap();
        let mut article = String::new();
        archive
            .by_name("OEBPS/article.xhtml")
            .unwrap()
            .read_to_string(&mut article)
            .unwrap();
        assert!(article.contains("https://example.test/?a=fish&amp;b=chips"));
    }
}

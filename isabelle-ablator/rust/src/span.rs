//! Command spans (Pure/PIDE/command_span.scala) + `parse_spans`
//! (Pure/Isar/outer_syntax.scala) + a `Syntax` bundling keywords and lexicons.

use crate::keyword::{Keywords, BEFORE_COMMAND};
use crate::token::{implode, Token};
use crate::tokenize::{explode, Lexicon};

#[derive(Clone, Debug)]
pub enum SpanKind {
    Command {
        name: String,
        keyword_kind: Option<String>,
    },
    Ignored,
    Malformed,
}

#[derive(Clone, Debug)]
pub struct Span {
    pub kind: SpanKind,
    pub content: Vec<Token>,
}

impl Span {
    /// Command name, or "" for ignored/malformed spans.
    pub fn name(&self) -> &str {
        match &self.kind {
            SpanKind::Command { name, .. } => name,
            _ => "",
        }
    }
    /// Keyword kind of a command span (None otherwise).
    pub fn keyword_kind(&self) -> Option<&str> {
        match &self.kind {
            SpanKind::Command { keyword_kind, .. } => keyword_kind.as_deref(),
            _ => None,
        }
    }
    /// `Token.implode` of the content — the span's source text.
    pub fn source(&self) -> String {
        implode(&self.content)
    }
}

/// Outer syntax for a session: keyword table + command/minor lexicons.
pub struct Syntax {
    pub keywords: Keywords,
    major: Lexicon,
    minor: Lexicon,
}

impl Syntax {
    pub fn new(keywords: Keywords) -> Syntax {
        let major = Lexicon::from_names(keywords.major_names());
        let minor = Lexicon::from_names(keywords.minor_names());
        Syntax {
            keywords,
            major,
            minor,
        }
    }

    pub fn hol() -> Syntax {
        Syntax::new(Keywords::hol())
    }

    pub fn tokenize(&self, text: &str) -> Vec<Token> {
        explode(&self.major, &self.minor, text)
    }

    fn is_before_command(&self, tok: &Token) -> bool {
        tok.is_keyword() && self.keywords.kind(&tok.source) == Some(BEFORE_COMMAND)
    }

    /// Group tokens into spans (Pure/Isar/outer_syntax.scala parse_spans).
    pub fn parse_spans(&self, text: &str) -> Vec<Span> {
        let toks = self.tokenize(text);
        let mut result: Vec<Span> = Vec::new();
        let mut content: Vec<Token> = Vec::new();
        let mut ignored: Vec<Token> = Vec::new();

        let ship = |result: &mut Vec<Span>, toks: Vec<Token>, kw: &Keywords| {
            let kind = if toks.iter().all(|t| t.is_ignored()) {
                SpanKind::Ignored
            } else if toks.iter().any(|t| t.is_error()) {
                SpanKind::Malformed
            } else {
                match toks.iter().find(|t| t.is_command()) {
                    None => SpanKind::Malformed,
                    Some(cmd) => SpanKind::Command {
                        name: cmd.source.clone(),
                        keyword_kind: kw.kind(&cmd.source).map(|s| s.to_string()),
                    },
                }
            };
            result.push(Span {
                kind,
                content: toks,
            });
        };
        let flush = |result: &mut Vec<Span>,
                     content: &mut Vec<Token>,
                     ignored: &mut Vec<Token>,
                     kw: &Keywords| {
            if !content.is_empty() {
                ship(result, std::mem::take(content), kw);
            }
            if !ignored.is_empty() {
                ship(result, std::mem::take(ignored), kw);
            }
        };

        for tok in toks {
            if tok.is_ignored() {
                ignored.push(tok);
            } else if self.is_before_command(&tok)
                || (tok.is_command()
                    && (!content.iter().any(|t| self.is_before_command(t))
                        || content.iter().any(|t| t.is_command())))
            {
                flush(&mut result, &mut content, &mut ignored, &self.keywords);
                content.push(tok);
            } else {
                content.append(&mut ignored);
                content.push(tok);
            }
        }
        flush(&mut result, &mut content, &mut ignored, &self.keywords);
        result
    }
}

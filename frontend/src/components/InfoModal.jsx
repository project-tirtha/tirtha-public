import React, { useEffect, useRef, useState } from 'react';
import Icon from './Icon';
import { useTranslation } from 'react-i18next';
import '../styles/InfoModal.css';
// One focus trap for every dialog in the app (internal design notes). This component
// keeps its own markup and animation; only the keyboard behaviour is shared,
// because four hand-written copies is how three of them quietly stop working.
import { useModalBehaviour } from '../hooks/useModalBehaviour';

function InfoModal({ isOpen, onClose, title, content, html, isLoading, error }) {
  const { t } = useTranslation();
  const [shouldRender, setShouldRender] = useState(isOpen);
  const [activeClass, setActiveClass] = useState('');
  
  const modalRef = useRef(null);

  /*
    The entry and exit animation. Opening is immediate; closing has to outlive
    the prop by the length of the CSS transition, or the panel vanishes instead
    of sliding away.

    The mount decision is adjusted during render rather than set from the
    effect: `setShouldRender(true)` in an effect meant the first frame after
    opening rendered nothing, so the transition started from an element that had
    only just appeared and the entry animation was skipped about half the time.
  */
  if (isOpen && !shouldRender) setShouldRender(true);
  // Starts the exit transition in the same render that closed it, rather than a
  // frame later -- a frame in which the panel is still fully opaque.
  if (!isOpen && activeClass) setActiveClass('');

  useEffect(() => {
    if (isOpen) {
      // One frame after mounting, so the browser has a "before" to animate from.
      const frameId = requestAnimationFrame(() => setActiveClass('info-modal--active'));
      return () => cancelAnimationFrame(frameId);
    }

    // Unmount only after the exit transition has run; matches the CSS duration.
    const timer = setTimeout(() => setShouldRender(false), 250);
    return () => clearTimeout(timer);
  }, [isOpen]);

  useModalBehaviour(isOpen && shouldRender, onClose, modalRef);

  if (!shouldRender) return null;

  // Simple Markdown Link parser to render clickable links
  const parseMarkdownLinks = (text) => {
    if (typeof text !== 'string') return text;
    
    const linkRegex = /\[([^\]]+)\]\(([^)]+)\)/g;
    const parts = [];
    let lastIndex = 0;
    let match;
    
    while ((match = linkRegex.exec(text)) !== null) {
      if (match.index > lastIndex) {
        parts.push(text.substring(lastIndex, match.index));
      }
      
      parts.push(
        <a 
          key={match.index} 
          href={match[2]} 
          target="_blank" 
          rel="noopener noreferrer"
          className="info-modal-link"
        >
          {match[1]}
        </a>
      );
      
      lastIndex = linkRegex.lastIndex;
    }
    
    if (lastIndex < text.length) {
      parts.push(text.substring(lastIndex));
    }
    
    return parts.length > 0 ? parts : text;
  };

  // Convert double newlines to paragraph tags
  const renderFormattedContent = (rawContent) => {
    if (typeof rawContent !== 'string') return rawContent;
    
    return rawContent.split(/\n\n+/).map((paragraph, index) => {
      const parsed = parseMarkdownLinks(paragraph.trim());
      return <p key={index} className="info-modal-text">{parsed}</p>;
    });
  };

  return (
    <div className={`info-modal-overlay ${activeClass}`} onClick={onClose}>
      <div 
        ref={modalRef}
        className="info-modal-container" 
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="info-modal-title"
      >
        <div className="info-modal-header">
          <h2 id="info-modal-title" className="info-modal-title">{title}</h2>
          <button 
            type="button" 
            className="info-modal-close-btn" 
            onClick={onClose}
            aria-label={t('a11y.closeDialog')}
          >
            <Icon name="close" />
          </button>
        </div>
        
        <div className="info-modal-body">
          {/*
            A canonical document, when there is one.

            `dangerouslySetInnerHTML` is the right tool and a poor name here:
            this markup is rendered from a template in this repository by
            `api.document`, through the same `_opened` helper the standalone
            page uses. It is not user input and never has been -- the only
            writers are whoever edits `tirtha/templates/tirtha/*.html`. The
            alternative, retyping the Terms into a locale file, is the failure
            `shell.document` was written to end: a rewritten legal document
            presented as the original is a second document nobody wrote.

            `content` is still the path for the app's own prose, which is
            translated and is not a legal text.
          */}
          {isLoading ? (
            <p className="info-modal-text">{t('a11y.loading')}</p>
          ) : error ? (
            <p className="info-modal-text">{error}</p>
          ) : html ? (
            <div
              className="info-modal-document"
              dangerouslySetInnerHTML={{ __html: html }}
            />
          ) : (
            renderFormattedContent(content)
          )}
        </div>

        <div className="info-modal-footer">
          <button 
            type="button" 
            className="btn btn-primary info-modal-ok-btn" 
            onClick={onClose}
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

export default InfoModal;

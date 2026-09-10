<script lang="ts">
  import { goto } from '$app/navigation';
  import { bookRoot, chapters, bookRootAbsolutePath, audioRoot } from '$lib/stores/bookState';
  import { selectBookDirectory } from '$lib/services/fs';
  import { loadProjectFromAbsolutePath } from '$lib/services/landingProject';
  import { onMount } from 'svelte';
  import { tick } from 'svelte';
  import { get } from 'svelte/store';
  import { isFileSystemAccessApiSupported } from '$lib/utils/feature-detection';
  import { clearStoredProjectHandle } from '$lib/services/persistence';
  import { applyProjectHandle, getStoredProjectAvailability, restoreStoredProject } from '$lib/services/projectSession';
  import { beginLandingAction, clearFlagAfterDelay } from '$lib/services/landingInteraction';
  import { syncBookRootFromHandle } from '$lib/services/fs';

  // Dev mode detection
  const isDev = import.meta.env.DEV;
  const DEV_TEST_FOLDER_PATH = 'C:\\Users\\Chad\\Documents\\Code\\Python\\Useful-Scripts\\Data\\Resources\\A-Practical-Guide-To-Evil\\Book-2';

  interface Star {
    orbital: number;
    x: number;
    y: number;
    yOrigin: number;
    speed: number;
    rotation: number;
    startRotation: number;
    id: number;
    collapseBonus: number;
    color: string;
    hoverPos: number;
    expansePos: number;
    prevR: number;
    prevX: number;
    prevY: number;
    originalY: number;
    explodeAngle: number; // angle for radial explosion
    explodeSpeed: number; // speed of explosion
    draw: () => void;
  }

  let isSupported = false;
  let blackholeContainer: HTMLDivElement;
  let centerHoverEl: HTMLDivElement;
  let canvas: HTMLCanvasElement;
  let animationId: number;
  let isOpen = false;
  let isPickingFolder = false;
  let hyperspaceCallback: (() => void) | null = null;
  let lastClickTime = 0;
  const DEBOUNCE_MS = 500; // Prevent rapid clicks
  const TARGET_FRAME_MS = 1000 / 30;
  const MAX_RENDER_DPR = 1.25;
  const MIN_STAR_COUNT = 500;
  const MAX_STAR_COUNT = 1200;
  
  // Stored project state
  let hasStoredProject = false;
  let storedProjectName = '';
  let isLoadingStoredProject = false;

  onMount(() => {
    isSupported = isFileSystemAccessApiSupported();
    
    // Check for stored project asynchronously
    if (isSupported) {
      checkForStoredProject();
    }
    
    initBlackhole();

    return () => {
      // Cleanup animation on unmount
      if (animationId) {
        cancelAnimationFrame(animationId);
      }
      // Clear any lingering references
      hyperspaceCallback = null;
    };
  });

  async function checkForStoredProject() {
    const availability = await getStoredProjectAvailability();
    if (availability.available) {
      hasStoredProject = true;
      storedProjectName = availability.name;
      return;
    }

    hasStoredProject = false;
    if ('shouldClear' in availability && availability.shouldClear) {
      console.log('Lost permission to stored project, clearing');
      await clearStoredProjectHandle().catch(() => {});
    }
  }

  async function continueWithStoredProject(event?: MouseEvent) {
    const gate = beginLandingAction({
      event,
      lastClickTime,
      debounceMs: DEBOUNCE_MS,
      isBusy: isLoadingStoredProject,
      busyLogMessage: 'Already loading stored project',
    });
    lastClickTime = gate.nextLastClickTime;
    if (!gate.proceed) {
      return;
    }

    try {
      isLoadingStoredProject = true;
      await tick();
      
      console.log('[Landing] Attempting to continue with stored project...');
      const restored = await restoreStoredProject({ requestPermission: true, touchLastAccessed: true });
      if (!restored.ok) {
        console.error('[Landing] No stored project found');
        hasStoredProject = false;
        if ('reason' in restored && restored.reason === 'permission_denied') {
          await clearStoredProjectHandle();
        }
        return;
      }

      console.log('[Landing] Found stored project:', restored.name);
      chapters.set([]);
      void syncBookRootFromHandle({
        handle: restored.handle,
        audioRootPath: get(audioRoot) || null,
      }).then((rootSync) => {
        bookRootAbsolutePath.set(rootSync.resolvedBookRoot);
      }).catch((error) => {
        console.warn('[Landing] Background root sync failed:', error);
      });
      
      // Trigger hyperspace animation before navigation
      if (hyperspaceCallback) {
        console.log('[Landing] Triggering hyperspace animation...');
        hyperspaceCallback();
      } else {
        goto('/chapter');
      }
    } catch (error) {
      console.error('[Landing] Error loading stored project:', error);
      
      // Reset state
      isOpen = false;
      
      const errorMsg = error instanceof Error ? error.message : 'Unknown error';
      console.error('Failed to load stored project:', errorMsg);
    } finally {
      clearFlagAfterDelay((value) => {
        isLoadingStoredProject = value;
      });
    }
  }

  async function selectNewProject(event?: MouseEvent) {
    const gate = beginLandingAction({
      event,
      lastClickTime,
      debounceMs: DEBOUNCE_MS,
      isBusy: isPickingFolder,
      busyLogMessage: 'Folder picker already open',
    });
    lastClickTime = gate.nextLastClickTime;
    if (!gate.proceed) {
      return;
    }

    try {
      isPickingFolder = true;
      await tick();
      
      // Small delay to ensure the click event is fully processed
      // and the browser recognizes it as a user gesture
      await new Promise(resolve => setTimeout(resolve, 50));
      
      console.log('[Landing] Opening folder picker...');
      const dirHandle = await selectBookDirectory();
      
      if (!dirHandle) {
        console.log('[Landing] Folder selection cancelled by user');
        return;
      }
      
      console.log('[Landing] Selected folder:', dirHandle.name);
      // Store the handle for use in other pages
      applyProjectHandle(dirHandle);
      chapters.set([]);
      void syncBookRootFromHandle({
        handle: dirHandle,
        audioRootPath: get(audioRoot) || null,
      }).then((rootSync) => {
        bookRootAbsolutePath.set(rootSync.resolvedBookRoot);
      }).catch((error) => {
        console.warn('[Landing] Background root sync failed:', error);
      });
      
      // Update stored project state
      hasStoredProject = true;
      storedProjectName = dirHandle.name;
      
      // Trigger hyperspace animation before navigation
      if (hyperspaceCallback) {
        console.log('[Landing] Triggering hyperspace animation...');
        hyperspaceCallback();
      } else {
        goto('/chapter');
      }
    } catch (error) {
      // Log the error for debugging
      console.error('[Landing] Error during folder selection or scanning:', error);
      
      // Reset all state to ensure clean recovery
      isOpen = false;
      
      // Show user-friendly error message
      const errorMsg = error instanceof Error ? error.message : 'Unknown error';
      
      // Only show alert if it's not an AbortError (user cancellation)
      if (!(error instanceof Error && error.name === 'AbortError')) {
        console.error('Failed to open folder:', errorMsg);
        // Using console.error instead of alert to avoid blocking the UI
        // Consider implementing a toast notification system for better UX
      }
    } finally {
      clearFlagAfterDelay((value) => {
        isPickingFolder = value;
      });
    }
  }

  async function selectDevTestFolder(event?: MouseEvent) {
    const gate = beginLandingAction({
      event,
      lastClickTime,
      debounceMs: DEBOUNCE_MS,
      isBusy: isLoadingStoredProject || isPickingFolder,
      busyLogMessage: 'Already loading or picking folder',
    });
    lastClickTime = gate.nextLastClickTime;
    if (!gate.proceed) {
      return;
    }

    try {
      isLoadingStoredProject = true;
      
      console.log('[Landing] Setting dev test folder path:', DEV_TEST_FOLDER_PATH);
      
      // Extract folder name from path
      const folderName = DEV_TEST_FOLDER_PATH.substring(DEV_TEST_FOLDER_PATH.lastIndexOf('\\') + 1);
      
      // Set the book root name (just the folder name)
      bookRoot.set(folderName);
      
      // Set the absolute path directly
      bookRootAbsolutePath.set(DEV_TEST_FOLDER_PATH);

      const loadResult = await loadProjectFromAbsolutePath({
        rootPath: DEV_TEST_FOLDER_PATH,
        audioRootPath: get(audioRoot) || null,
      });
      const chapterList = loadResult.chapters;
      chapters.set(chapterList);
      const parsedCount = chapterList.filter((ch) => ch.parsed).length;
      console.log('[Landing] Loaded', chapterList.length, 'chapters from backend', parsedCount > 0 ? `(${parsedCount} parsed)` : '');
      
      // Update stored project state
      hasStoredProject = false; // We don't have a handle to store
      storedProjectName = folderName;
      
      // Trigger hyperspace animation before navigation
      if (hyperspaceCallback) {
        console.log('[Landing] Triggering hyperspace animation...');
        hyperspaceCallback();
      }
    } catch (error) {
      console.error('[Landing] Error setting dev test folder:', error);
      isOpen = false;
      
      const errorMsg = error instanceof Error ? error.message : 'Unknown error';
      console.error('Failed to set dev test folder:', errorMsg);
    } finally {
      clearFlagAfterDelay((value) => {
        isLoadingStoredProject = value;
      });
    }
  }

  function initBlackhole() {
    if (!blackholeContainer) return;

    const h = blackholeContainer.offsetHeight;
    const w = blackholeContainer.offsetWidth;
    const cw = w;
    const ch = h;
    const maxorbit = 255; // distance from center
    const centery = ch / 2;
    const centerx = cw / 2;

    const startTime = performance.now();
    let currentTime = 0;
    let lastFrameTime = 0;

    const stars: Star[] = [];
    let collapse = false; // if hovered
    let expanse = false; // if clicked
    let returning = false; // if particles are returning to orbit
    let implode = false; // if implosion animation is active
    let implodeStartTime = 0;
    let explode = false; // explosion animation (stars burst outward)
    let explodeStartTime = 0;
    let whitePulse = false; // white circle expansion
    let whitePulseStartTime = 0;

    // Create canvas
    canvas = document.createElement('canvas');
    blackholeContainer.appendChild(canvas);
    const context = canvas.getContext("2d");

    if (!context) return;

    context.globalCompositeOperation = "multiply";

    function getRenderDpr() {
      return Math.min(window.devicePixelRatio || 1, MAX_RENDER_DPR);
    }

    function setCanvasResolution(targetCanvas: HTMLCanvasElement, width: number, height: number) {
      if (!context) return;
      const renderDpr = getRenderDpr();
      targetCanvas.style.width = `${width}px`;
      targetCanvas.style.height = `${height}px`;
      targetCanvas.width = Math.max(1, Math.floor(width * renderDpr));
      targetCanvas.height = Math.max(1, Math.floor(height * renderDpr));
      context.setTransform(renderDpr, 0, 0, renderDpr, 0, 0);
    }

    function getStarCount(width: number, height: number) {
      const viewportArea = width * height;
      const scaledCount = Math.round(viewportArea / 1600);
      return Math.max(MIN_STAR_COUNT, Math.min(MAX_STAR_COUNT, scaledCount));
    }

    function rotate(cx: number, cy: number, x: number, y: number, angle: number) {
      const radians = angle;
      const cos = Math.cos(radians);
      const sin = Math.sin(radians);
      const nx = (cos * (x - cx)) + (sin * (y - cy)) + cx;
      const ny = (cos * (y - cy)) - (sin * (x - cx)) + cy;
      return [nx, ny];
    }

    setCanvasResolution(canvas, cw, ch);
    const starCount = getStarCount(cw, ch);

    function createStar(): Star {
      // Get a weighted random number, so that the majority of stars will form in the center of the orbit
      const rands = [];
      rands.push(Math.random() * (maxorbit / 2) + 1);
      rands.push(Math.random() * (maxorbit / 2) + maxorbit);

      const orbital = (rands.reduce((p, c) => p + c, 0) / rands.length);
      
      const x = centerx; // All of these stars are at the center x position at all times
      const y = centery + orbital; // Set Y position starting at the center y + the position in the orbit

      const yOrigin = centery + orbital; // this is used to track the particles origin

      const speed = (Math.floor(Math.random() * 2.5) + 1.5) * Math.PI / 180; // The rate at which this star will orbit
      const rotation = 0; // current Rotation
      const startRotation = (Math.floor(Math.random() * 360) + 1) * Math.PI / 180; // Starting rotation

      const id = stars.length; // This will be used when expansion takes place

      let collapseBonus = orbital - (maxorbit * 0.7); // This "bonus" is used to randomly place some stars outside of the blackhole on hover
      if (collapseBonus < 0) { // if the collapse "bonus" is negative
        collapseBonus = 0; // set it to 0, this way no stars will go inside the blackhole
      }

      const color = 'rgba(255,255,255,' + (1 - ((orbital) / 255)) + ')'; // Color the star white, but make it more transparent the further out it is generated

      const hoverPos = centery + (maxorbit / 2) + collapseBonus; // Where the star will go on hover of the blackhole
      const expansePos = centery + (id % 100) * -10 + (Math.floor(Math.random() * 20) + 1); // Where the star will go when expansion takes place

      const prevR = startRotation;
      const prevX = x;
      const prevY = y;
      
      // Store original position for returning
      const originalY = yOrigin;
      
      // Random angle for radial explosion
      const explodeAngle = Math.random() * Math.PI * 2;
      const explodeSpeed = Math.random() * 9 + 12; // 6-14 pixels per frame (much faster explosion)

      const star: Star = {
        orbital,
        x,
        y,
        yOrigin,
        speed,
        rotation,
        startRotation,
        id,
        collapseBonus,
        color,
        hoverPos,
        expansePos,
        prevR,
        prevX,
        prevY,
        originalY,
        explodeAngle,
        explodeSpeed,
        draw: function() {
          if (implode) {
            // Implosion animation - particles collapse inward with increasing speed
            this.rotation = this.startRotation + (currentTime * (this.speed / 2));
            
            // Calculate distance from center
            const dx = this.x - centerx;
            const dy = this.y - centery;
            const currentDistance = Math.sqrt(dx * dx + dy * dy);
            
            if (currentDistance > 2) {
              // Exponential acceleration as we get closer to center
              // The closer we are, the faster we move
              const distanceRatio = currentDistance / this.orbital; // 1 to 0 as we approach center
              const acceleration = Math.pow(1 - distanceRatio, 2.5); // 0 to 1, increases exponentially
              const speed = 1 + (acceleration * 12); // Speed increases from 1 to 13 (slower)
              
              // Move toward center
              const angle = Math.atan2(dy, dx);
              this.x -= Math.cos(angle) * speed;
              this.y -= Math.sin(angle) * speed;
            } else {
              // Snap to center
              this.x = centerx;
              this.y = centery;
            }
            
            if (!context) return;
            
            context.save();
            context.fillStyle = this.color;
            context.strokeStyle = this.color;
            context.beginPath();
            const oldPos = rotate(centerx, centery, this.prevX, this.prevY, -this.prevR);
            context.moveTo(oldPos[0], oldPos[1]);
            context.translate(centerx, centery);
            context.rotate(this.rotation);
            context.translate(-centerx, -centery);
            context.lineTo(this.x, this.y);
            context.stroke();
            context.restore();

            this.prevR = this.rotation;
            this.prevX = this.x;
            this.prevY = this.y;
            return;
          }
          
          if (explode) {
            // Explosion animation - particles burst outward radially from center
            this.rotation = this.startRotation + (currentTime * (this.speed / 2));
            
            // Move outward from center along the explosion angle
            this.x += Math.cos(this.explodeAngle) * this.explodeSpeed;
            this.y += Math.sin(this.explodeAngle) * this.explodeSpeed;
            
            if (!context) return;
            
            context.save();
            context.fillStyle = this.color;
            context.strokeStyle = this.color;
            context.beginPath();
            const oldPos = rotate(centerx, centery, this.prevX, this.prevY, -this.prevR);
            context.moveTo(oldPos[0], oldPos[1]);
            context.translate(centerx, centery);
            context.rotate(this.rotation);
            context.translate(-centerx, -centery);
            context.lineTo(this.x, this.y);
            context.stroke();
            context.restore();

            this.prevR = this.rotation;
            this.prevX = this.x;
            this.prevY = this.y;
            return;
          }
          
          if (!expanse && !returning) {
            this.rotation = this.startRotation + (currentTime * this.speed);
            if (!collapse) { // not hovered
              if (this.y > this.yOrigin) {
                this.y -= 2.5;
              }
              if (this.y < this.yOrigin - 4) {
                this.y += (this.yOrigin - this.y) / 10;
              }
            } else { // on hover
              if (this.y > this.hoverPos) {
                this.y -= (this.hoverPos - this.y) / -5;
              }
              if (this.y < this.hoverPos - 4) {
                this.y += 2.5;
              }
            }
          } else if (expanse && !returning) {
            this.rotation = this.startRotation + (currentTime * (this.speed / 2));
            if (this.y > this.expansePos) {
              this.y -= Math.floor(this.expansePos - this.y) / -80; // Slower expansion for better visibility
            }
          } else if (returning) {
            // Returning to original orbit slowly
            this.rotation = this.startRotation + (currentTime * this.speed);
            if (Math.abs(this.y - this.originalY) > 2) {
              this.y += (this.originalY - this.y) / 50; // Much slower return
            } else {
              this.y = this.originalY;
              this.yOrigin = this.originalY;
            }
          }

          if (!context) return;

          context.save();
          context.fillStyle = this.color;
          context.strokeStyle = this.color;
          context.beginPath();
          const oldPos = rotate(centerx, centery, this.prevX, this.prevY, -this.prevR);
          context.moveTo(oldPos[0], oldPos[1]);
          context.translate(centerx, centery);
          context.rotate(this.rotation);
          context.translate(-centerx, -centery);
          context.lineTo(this.x, this.y);
          context.stroke();
          context.restore();

          this.prevR = this.rotation;
          this.prevX = this.x;
          this.prevY = this.y;
        }
      };

      stars.push(star);
      return star;
    }

    // Event listeners
    if (centerHoverEl) {
      // Only trigger expansion animation on direct centerHover clicks (not button clicks)
      centerHoverEl.addEventListener('click', function(e) {
        // Ignore clicks on the button element
        if (e.target && (e.target as HTMLElement).tagName === 'BUTTON') {
          return;
        }
        
        collapse = false;
        expanse = true;
        returning = false;
        isOpen = true;
        
        // Start the return cycle after full expansion (20-30 seconds)
        setTimeout(() => {
          expanse = false;
          returning = true;
          
          // After particles return, reset to normal orbit
          setTimeout(() => {
            returning = false;
            isOpen = false;
          }, 8000); // 8 seconds to return slowly
        }, 25000); // 25 seconds of expansion experience
      });
      
      centerHoverEl.addEventListener('mouseover', function() {
        if (expanse === false) {
          collapse = true;
        }
      });
      
      centerHoverEl.addEventListener('mouseout', function() {
        if (expanse === false) {
          collapse = false;
        }
      });
    }

    // Animation loop
    function loop(frameTime: number) {
      if (document.hidden) {
        animationId = requestAnimationFrame(loop);
        return;
      }

      if (lastFrameTime !== 0 && frameTime - lastFrameTime < TARGET_FRAME_MS) {
        animationId = requestAnimationFrame(loop);
        return;
      }

      lastFrameTime = frameTime;

      currentTime = (frameTime - startTime) / 50;

      if (!context) return;

      // White pulse expansion (on top of explosion)
      if (whitePulse) {
        const pulseProgress = (currentTime - whitePulseStartTime) / 120;
        
        // First draw the explosion stars if still exploding
        if (explode) {
          context.fillStyle = 'rgba(25,25,25,0.2)';
          context.fillRect(0, 0, cw, ch);
        } else {
          // Clear to black if explosion finished
          context.fillStyle = 'rgba(0,0,0,1)';
          context.fillRect(0, 0, cw, ch);
        }
        
        // Draw explosion stars if active (on lower layer)
        if (explode) {
          for (let i = 0; i < stars.length; i++) {
            if (stars[i] !== undefined) {
              stars[i].draw();
            }
          }
        }
        
        // Draw expanding white circle (on top layer)
        const maxRadius = Math.sqrt(cw * cw + ch * ch); // Diagonal of screen
        const radius = pulseProgress * maxRadius * 3; // Fast expansion
        
        context.save();
        context.fillStyle = 'rgba(255,255,255,1)';
        context.beginPath();
        context.arc(centerx, centery, radius, 0, Math.PI * 2);
        context.fill();
        context.restore();
        
        // Navigate after white fills screen
        if (radius > maxRadius) {
          goto('/chapter');
          return;
        }
        
        // Continue to draw explosion, so skip the normal star drawing below
        animationId = requestAnimationFrame(loop);
        return;
      }
      // During implosion, fade to black as everything collapses
      else if (implode) {
        const implodeProgress = (currentTime - implodeStartTime) / 30;
        // Gradually fade to black as particles collapse
        const fadeProgress = Math.min(implodeProgress / 1.5, 1);
        const fadeOpacity = 0.2 + (fadeProgress * 0.6); // Fade from 0.2 to 0.8
        
        context.fillStyle = `rgba(0,0,0,${fadeOpacity})`;
        context.fillRect(0, 0, cw, ch);
        
        // Check if implosion is complete, then immediately trigger explosion
        if (implodeProgress > 1.0) {
          // Minimal delay (1 frame)
          if (implodeProgress > 1.03) {
            implode = false;
            explode = true;
            explodeStartTime = currentTime;
            // Start white pulse at the same time as explosion
            whitePulse = true;
            whitePulseStartTime = currentTime;
          }
        }
      }
      // During explosion
      else if (explode) {
        const explodeProgress = (currentTime - explodeStartTime) / 30;
        
        context.fillStyle = 'rgba(25,25,25,0.2)';
        context.fillRect(0, 0, cw, ch);
      } else {
        context.fillStyle = 'rgba(25,25,25,0.2)'; // somewhat clear the context, this way there will be trails behind the stars
        context.fillRect(0, 0, cw, ch);
      }

      // Draw stars (handled separately during white pulse)
      if (!whitePulse) {
        for (let i = 0; i < stars.length; i++) { // For each star
          if (stars[i] !== undefined) {
            stars[i].draw(); // Draw it
          }
        }
      }

      animationId = requestAnimationFrame(loop);
    }

    function init() {
      if (!context) return;
      context.fillStyle = 'rgba(25,25,25,1)'; // Initial clear of the canvas
      context.fillRect(0, 0, cw, ch);
      for (let i = 0; i < starCount; i++) {
        createStar();
      }
      animationId = requestAnimationFrame(loop);
    }

    // Set up implosion callback
    hyperspaceCallback = () => {
      collapse = false;
      expanse = false;
      returning = false;
      implode = true;
      implodeStartTime = currentTime;
      explode = false;
      whitePulse = false;
      isOpen = true;
    };

    init();
  }
</script>

<main>
  <div id="blackhole" bind:this={blackholeContainer}>
    <div class="centerHover" class:open={isOpen} bind:this={centerHoverEl}>
    {#if isSupported}
      {#if hasStoredProject}
        <!-- Show Continue and New options when a project exists -->
        <div class="button-container">
          <div class="title">OpenBook</div>
          <div class="actions">
            <button 
              on:click={continueWithStoredProject} 
              disabled={isLoadingStoredProject || isPickingFolder}
              type="button"
              class="primary-action"
            >
              {isLoadingStoredProject ? 'Loading...' : 'Continue'}
            </button>
            <button 
              on:click={selectNewProject} 
              disabled={isLoadingStoredProject || isPickingFolder}
              type="button"
              class="secondary-action"
            >
              New
            </button>
          </div>
          {#if storedProjectName}
            <div class="project-name">{storedProjectName}</div>
          {/if}
        </div>
      {:else}
        <!-- Show simple OpenBook button for first time -->
        <button 
          on:click={selectNewProject} 
          disabled={isPickingFolder}
          type="button"
        >
          {isPickingFolder ? 'Opening...' : 'OpenBook'}
        </button>
      {/if}
    {:else}
        <span class="error-text">Unsupported Browser</span>
    {/if}
  </div>
  </div>
  
  <!-- Hidden dev button for testing -->
  {#if isDev}
    <button 
      class="dev-test-button"
      on:click={selectDevTestFolder}
      disabled={isLoadingStoredProject || isPickingFolder}
      type="button"
      title="Dev: Set test folder path"
    >
      &gt;
    </button>
  {/if}
</main>

<style>
  :global(body), :global(html) { 
    height: 100%; 
    margin: 0;
    padding: 0;
  }

  main {
    height: 100vh;
    background-color: rgba(25,25,25,1);
    overflow: hidden;
  }

  #blackhole {
    height: 100%;
    width: 100%;
    position: relative;
    display: flex;
  }

  .centerHover {
    width: 255px;
    height: 255px;
    background-color: transparent;
    border-radius: 50%;
    position: absolute;
    left: 50%;
    top: 50%;
    margin-top: -128px;
    margin-left: -128px;
    z-index: 2;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 500ms;
  }

  .centerHover.open {
    opacity: 0;
    pointer-events: none;
  }

  .button-container {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
  }

  .title {
    color: #666;
    font-family: serif;
    font-size: 18px;
    transition: all 500ms;
    position: relative;
  }

  .title:before {
    content: '';
    position: absolute;
    left: -28px;
    top: 50%;
    transform: translateY(-50%);
    height: 1px;
    width: 16px;
    background-color: #666;
    transition: all 500ms;
  }

  .title:after {
    content: '';
    position: absolute;
    right: -28px;
    top: 50%;
    transform: translateY(-50%);
    height: 1px;
    width: 16px;
    background-color: #666;
    transition: all 500ms;
  }

  .centerHover:hover .title {
    color: #DDD;
  }

  .centerHover:hover .title:before,
  .centerHover:hover .title:after {
    background-color: #DDD;
  }

  .actions {
    display: flex;
    gap: 16px;
  }

  .project-name {
    color: #555;
    font-family: serif;
    font-size: 12px;
    font-style: italic;
    transition: all 500ms;
  }

  .centerHover:hover .project-name {
    color: #999;
  }

  .centerHover button {
    color: #666;
    font-family: serif;
    font-size: 16px;
    position: relative;
    transition: all 500ms;
    background: transparent;
    border: none;
    cursor: pointer;
    padding: 8px 16px;
  }

  .centerHover button.primary-action {
    font-size: 18px;
    font-weight: 500;
  }

  .centerHover button.secondary-action {
    font-size: 14px;
    opacity: 0.8;
  }

  .centerHover button:disabled {
    color: #444;
    cursor: not-allowed;
    opacity: 0.6;
    pointer-events: none; /* Prevent any interaction when disabled */
  }

  .centerHover:hover button {
    color: #DDD;
  }

  /* Single button style (first time user) */
  .centerHover > button {
    font-size: 18px;
  }

  .centerHover > button:before {
    content: '';
    display: inline-block;
    height: 1px;
    width: 16px;
    margin-right: 12px;
    margin-bottom: 4px;
    background-color: #666;
    transition: all 500ms;
  }

  .centerHover > button:after {
    content: '';
    display: inline-block;
    height: 1px;
    width: 16px;
    margin-left: 12px;
    margin-bottom: 4px;
    background-color: #666;
    transition: all 500ms;
  }

  .centerHover:hover > button:before,
  .centerHover:hover > button:after { 
    background-color: #DDD; 
  }

  .error-text {
    color: #ff4444;
    font-family: serif;
    font-size: 14px;
    text-align: center;
    padding: 0 20px;
  }

  :global(canvas) {
    position: relative;
    z-index: 1;
    width: 100%;
    height: 100%;
    margin: auto;
  }

  .dev-test-button {
    position: fixed;
    bottom: 10px;
    right: 10px;
    width: 30px;
    height: 30px;
    background: rgba(100, 100, 100, 0.1);
    border: 1px solid rgba(150, 150, 150, 0.2);
    border-radius: 50%;
    cursor: pointer;
    z-index: 1000;
    opacity: 0.1;
    transition: opacity 0.2s;
    font-size: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0;
  }

  .dev-test-button:hover {
    opacity: 0.8;
    background: rgba(100, 100, 100, 0.3);
  }

  .dev-test-button:disabled {
    opacity: 0.05;
    cursor: not-allowed;
  }
</style>
